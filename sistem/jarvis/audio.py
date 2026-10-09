"""Mikrofon yakalama, kısık sesi dengeleme ve hoparlör çalma.

Asıl JARVIS'te ses düzeyi her 32 ms'lik parçada saf Python döngüsüyle hesaplanıyordu (hem mikrofonda hem
hoparlörde); burada numpy ile yapılır. Dengeleme ve dalgalanma tamponunun davranışı aynıdır (gerçek kullanımda
"ses sorunu çözüldü" denmiş ayarlar korunur).
"""

from __future__ import annotations

import asyncio
import collections
import time

import numpy as np
import pyaudio

import audio_jitter

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_RATE = 16000
RECV_RATE = 24000
BLOCK_BYTES = 1024 * 2           # hoparlöre yazılan blok (≈21 ms)

_RESTART = object()               # hoparlör kuyruğunda "aygıtı yeniden aç" işareti


class AudioGate:
    """Ses aygıtı değişince mikrofon ve hoparlör birlikte kapanıp yeniden açılır.

    PortAudio aygıt listesini ve varsayılan aygıtı yalnız ilk başlatılışta okur ve açık her PyAudio nesnesini sayar:
    listenin yenilenmesi için İKİSİNİN de kapanması (sayaç sıfır), sonra açılması gerekir. hold açıkken kimse yeniden
    açmaz; restart() ikisinin de kapandığını görünce hold'u kaldırır."""

    def __init__(self):
        self.hold = False

    async def restart(self, mic: "Microphone", player: "Player", timeout: float = 3.0) -> bool:
        self.hold = True
        try:
            mic.request_restart()
            player.request_restart()
            end = time.monotonic() + timeout
            while not (mic.closed and player.closed) and time.monotonic() < end:
                await asyncio.sleep(0.05)
            return mic.closed and player.closed
        finally:
            self.hold = False

    async def wait(self):
        while self.hold:
            await asyncio.sleep(0.05)


def rms_int16(data: bytes) -> float:
    a = np.frombuffer(data, dtype="<i2")
    if a.size == 0:
        return 0.0
    f = a.astype(np.float32)
    return float(np.sqrt(np.mean(f * f)))


class MicLeveler:
    """Kısık konuşmayı yumuşakça yükseltir; sessizliği ve dip gürültüsünü büyütmez, taşmayı önler."""
    TARGET_RMS = 800.0
    MAX_GAIN = 3.0
    ATTACK = 0.1

    def __init__(self):
        self.gain = 1.0
        self.noise = None

    def process(self, data: bytes, rms: float) -> bytes:
        if self.noise is None:
            self.noise = rms
        else:
            self.noise += (rms - self.noise) * (0.3 if rms < self.noise else 0.01)
        speech = rms > max(120.0, self.noise * 2.5)
        target = min(self.MAX_GAIN, self.TARGET_RMS / rms) if speech and rms < self.TARGET_RMS else 1.0
        new = self.gain + (target - self.gain) * (0.6 if target < self.gain else self.ATTACK)
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
        start = self.gain
        peak = float(np.abs(samples).max()) if samples.size else 0.0
        if peak:
            limit = max(1.0, 32000.0 / peak)
            start, new = min(start, limit), max(1.0, min(new, limit))
        self.gain = new
        if abs(start - 1.0) < 1e-3 and abs(new - 1.0) < 1e-3:
            return data
        n = samples.size or 1
        ramp = start + (new - start) * (np.arange(1, n + 1, dtype=np.float32) / n)
        return np.clip(samples * ramp, -32768, 32767).astype("<i2").tobytes()


class Microphone:
    """Varsayılan giriş aygıtını açar (olmazsa diğerlerini dener); her ~32 ms'lik parçayı on_frame'e verir.
    PortAudio geri çağrısı ağdan bağımsız çalışır; parçalar asyncio döngüsüne aktarılır."""

    def __init__(self, on_frame, gate: AudioGate | None = None):
        self.on_frame = on_frame          # on_frame(data: bytes, rate: int, overflow: bool) — döngüde çağrılır
        self.gate = gate or AudioGate()
        self.device_name = ""
        self.rate = SEND_RATE
        self.last_frame = 0.0
        self.closed = True
        self._restart = False
        self._stream = None
        self._pa = None

    def request_restart(self):
        self._restart = True

    async def run(self, on_ready=None):
        """Aygıt değişince (request_restart) akışı kapatıp varsayılan aygıtla yeniden açar."""
        while True:
            await self.gate.wait()
            self._restart = False
            await self._run_once(on_ready)

    async def _run_once(self, on_ready=None):
        loop = asyncio.get_running_loop()
        self.closed = False
        self._pa = pyaudio.PyAudio()
        self.last_frame = time.monotonic()
        try:
            pa = self._pa
            devices = [pa.get_device_info_by_index(i) for i in range(pa.get_device_count())]
            inputs = [d for d in devices if d.get("maxInputChannels", 0) > 0]
            try:
                default = pa.get_default_input_device_info()
            except OSError:
                default = None
            if not inputs:
                raise RuntimeError("Mikrofon bulunamadı. System Settings › Sound › Input bölümünü kontrol et.")
            ordered = ([default] if default else []) + [d for d in inputs if not default or d["index"] != default["index"]]
            failures = []
            for device in ordered:
                for rate in dict.fromkeys([SEND_RATE, int(device["defaultSampleRate"])]):
                    frames = max(256, int(rate * .032))

                    def capture(data, frame_count, timing, status, capture_rate=rate):
                        try:
                            loop.call_soon_threadsafe(self._deliver, data, capture_rate, bool(status))
                        except RuntimeError:
                            return (None, pyaudio.paComplete)
                        return (None, pyaudio.paContinue)
                    try:
                        self._stream = await asyncio.to_thread(
                            pa.open, format=FORMAT, channels=CHANNELS, rate=rate, input=True,
                            input_device_index=int(device["index"]), frames_per_buffer=frames,
                            stream_callback=capture)
                        self.device_name, self.rate = str(device["name"]), rate
                        break
                    except (OSError, ValueError) as exc:
                        failures.append(f"{device['name']} ({rate} Hz): {exc}")
                if self._stream is not None:
                    break
            if self._stream is None:
                raise RuntimeError("Mikrofon açılamadı: " + " | ".join(failures)[:300])
            if on_ready:
                on_ready(self.device_name, self.rate)
            while not self._restart:
                await asyncio.sleep(.1)
                if not self._stream.is_active() or time.monotonic() - self.last_frame > 3:
                    raise RuntimeError("Mikrofon akışı durdu; yeniden açılıyor.")
        finally:
            if self._stream is not None:
                try:
                    await asyncio.to_thread(self._stream.close)
                except Exception:
                    pass
                self._stream = None
            try:
                self._pa.terminate()
            except Exception:
                pass
            self.closed = True

    def _deliver(self, data, rate, overflow):
        self.last_frame = time.monotonic()
        self.on_frame(data, rate, overflow)


class Player:
    """Gemini'den gelen 24 kHz sesi çalar. Cevabın başında ~0,2 sn, akış ortada kuruyunca ~0,12 sn biriktirir
    (audio_jitter); kesilme olursa kayda [SES] satırı yazar. None = cevabın sonu."""

    def __init__(self, on_level, on_speaking, gate: AudioGate | None = None):
        self.gate = gate or AudioGate()
        self.closed = True
        self._restart = False             # interrupt() kuyruğu boşaltıp işareti silse de yeniden açma unutulmasın
        self.queue: asyncio.Queue = None
        self.generation = 0
        self.on_level = on_level              # on_level(0..1)
        self.on_speaking = on_speaking        # on_speaking(bool)
        self.discard = False
        self.blocked = lambda: False          # sessize alındı / duraklatıldı mı

    def interrupt(self):
        """Çalan cevabın kalanını bırak (kesilme, "Jarvis sus", F7)."""
        self.generation += 1
        if self.queue is not None:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)

    def request_restart(self):
        """Çalmakta olan parçalar bitince akış kapanır ve varsayılan çıkış aygıtıyla yeniden açılır."""
        self._restart = True
        if self.queue is not None:
            self.queue.put_nowait(_RESTART)

    async def run(self):
        self.queue = asyncio.Queue()
        held = collections.deque()
        while True:
            await self.gate.wait()
            await self._run_once(held)

    async def _run_once(self, held):
        self.closed = False
        self._restart = False
        pa = pyaudio.PyAudio()
        try:
            stream = await asyncio.to_thread(pa.open, format=FORMAT, channels=CHANNELS, rate=RECV_RATE, output=True)
        except BaseException:
            pa.terminate()
            self.closed = True
            raise
        clock = audio_jitter.PlayClock(RECV_RATE)
        diag = {"started": False, "gaps": 0, "worst": 0.0, "kind": "", "logged_at": 0.0}

        def note_gap(gap, kind):
            diag["gaps"] += 1
            if gap > diag["worst"]:
                diag["worst"], diag["kind"] = gap, kind
            now = time.monotonic()
            if now - diag["logged_at"] > 2.0:
                diag["logged_at"] = now
                print(f"[SES] kesilme: {gap * 1000:.0f} ms ({kind})", flush=True)

        def stop_playing():
            diag["started"] = False
            clock.reset()

        try:
            while True:
                t0 = time.perf_counter()
                chunk = held.popleft() if held else await self.queue.get()
                waited = time.perf_counter() - t0
                if chunk is _RESTART and not self._restart:
                    continue                   # eski işaret (yeniden açma zaten yapıldı)
                if self._restart:
                    if chunk is not _RESTART:
                        held.appendleft(chunk)   # parça kaybolmasın: yeni aygıtta çalınır
                    stop_playing()
                    return
                if chunk is None:
                    if diag["gaps"]:
                        print(f"[SES] konuşma bitti: {diag['gaps']} kesilme, en uzun {diag['worst'] * 1000:.0f} ms "
                              f"({diag['kind']})", flush=True)
                    diag.update(gaps=0, worst=0.0, kind="")
                    stop_playing()
                    self.on_speaking(False)
                    continue
                if self.blocked() or self.discard:
                    stop_playing()
                    continue
                generation = self.generation
                if not diag["started"] or waited > audio_jitter.REPRIME_AFTER_S:
                    chunk, extra = await audio_jitter.gather(
                        self.queue, chunk, audio_jitter.PREROLL_S if not diag["started"] else audio_jitter.REPRIME_S)
                    held.extend(extra)
                    if generation != self.generation or self.discard:
                        stop_playing()
                        continue
                self.on_speaking(True)
                first = True
                for offset in range(0, len(chunk), BLOCK_BYTES):
                    if generation != self.generation:
                        stop_playing()
                        break
                    block = chunk[offset:offset + BLOCK_BYTES]
                    self.on_level(min(1.0, rms_int16(block) / 7200.0))
                    gap = clock.before_write(time.perf_counter(), len(block))
                    if gap >= 0.008 and diag["started"]:
                        note_gap(gap, "veri gecikti" if first and waited > gap * 0.5 else "çalma gecikti")
                    await asyncio.to_thread(stream.write, block)
                    diag["started"] = True
                    first = False
        finally:
            self.on_speaking(False)
            try:
                stream.close()
            except Exception:
                pass
            try:
                pa.terminate()
            except Exception:
                pass
            self.closed = True
