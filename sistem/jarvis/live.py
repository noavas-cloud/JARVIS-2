"""JARVIS 2 çekirdeği: Gemini Live oturumu, mikrofon/hoparlör, araç çağrıları.

Asıl JARVIS'e göre farklar:
  • Oturum devamı (session resumption): bağlantı koparsa ya da sunucu oturumu yenilerse aynı konuşma bağlamıyla
    geri bağlanılır; JARVIS az önce konuşulanı unutmaz.
  • Araç çağrıları alım döngüsünü bekletmez (ayrı görevde çalışır); bu sırada kesilme ve oturum yenileme işlenir.
  • Arayüzle iletişim yalnız State üzerinden (iş parçacığı güvenli).
  • Uzun/arka plan işleri (görev zinciri, paralel görevler, kesintiden devam, takipler, bildirimler, Telegram,
    Vapi) jarvis/tasks.py'de (self.tasks).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import threading
import time
import traceback

from google import genai
from google.genai import types

from jarvis import audio, prompt, tools
from jarvis.audio import AudioGate, Microphone, MicLeveler, Player, rms_int16
from jarvis.brain_host import BrainHost
from jarvis.camera import WebcamStreamer
from jarvis.phone_host import PhoneHost
from jarvis.paths import ensure_import_path
from jarvis.state import State
from jarvis.tasks import UNLOCKED_TOOLS, TaskHost

ensure_import_path()

from app_config import get_app_config_value  # noqa: E402
from actions.microphone_command import microphone_command  # noqa: E402
from actions.chrome_research import ResearchHost  # noqa: E402
from actions.offline_commands import parse_command, resolve_file_command, spoken_result  # noqa: E402
from actions.offline_audio import OfflineUtterances  # noqa: E402
from actions.screen_watch import ScreenWatcher  # noqa: E402
from actions.stop_command import StopCommandListener  # noqa: E402
from memory.conversation_history import TOOL_NAMES, ConversationHistory  # noqa: E402

# 04.10.2026 (kullanıcı onayı): anahtarla ölçüldü — 3.1-flash-live-preview cevaba ~2 kat daha hızlı başladı (1,5 sn / 5,4 sn;
# araçlı soruda ~1,9 / ~3,5 sn), araçlar + döküm + oturum devamı çalışıyor. "Önizleme" olduğu için bağlanamazsa (art arda 2
# kısa/başarısız deneme) eski modele düşülür. Ayarlarda "live_model" verilirse o kullanılır. (gemini-3.8-live JARVIS
# ayarlarıyla ses döndürmeden kapandı — kullanma.)
DEFAULT_MODEL = "models/gemini-3.1-flash-live-preview"
FALLBACK_MODEL = "models/gemini-2.5-flash-native-audio-latest"
TRANSCRIPTION_LANGUAGES = ["tr-TR", "en-US"]
TRANSCRIPTION_VOCABULARY = ["Jarvis", "İkinci Beyin", "Spotify", "WhatsApp", "ChatGPT", "Safari", "Finder", "YouTube",
                            "Telegram"]
# İnternetsiz komutlarda Whisper'a verilen ipucu: beklenen komutları sayınca kısa komutlar çok daha iyi anlaşılıyor
# (03.10, Yelda sesiyle 14 komut: eski ipucu 6/14, bu 14/14; komut olmayan 4 cümle yine komut sayılmadı).
OFFLINE_PROMPT = ("Jarvis komutları: Merhaba. Safari'yi aç. Spotify'ı aç. Hesap makinesini aç. Sesi azalt. Sesi artır. "
                  "Sesi kapat. Sesi elli yap. Pil durumu. Şarj ne kadar. Saat kaç. Son işlemi geri al. "
                  "Görevler ne durumda. Dosyayı bul.")
INPUT_TAG_RE = re.compile(r"<(?:noise|ctrl\d+)>", re.IGNORECASE)
CONTROL_TOKEN_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
SUCCESS_SFX_TOOLS = {"open_app", "add_calendar_event", "add_reminder", "delete_calendar_event"}
HISTORY_REFRESH_TOOLS = {"undo_action", "move_file", "rename_file", "trash_file", "context_control",
                         "add_calendar_event", "delete_calendar_event", "add_reminder"}


def join_transcript(parts) -> str:
    """Döküm parçaları kelime ortasından gelir ("Ja","rvis"); boşluklar parçaların içindedir."""
    return " ".join(INPUT_TAG_RE.sub(" ", "".join(parts)).split())


def clean_output(text: str) -> str:
    raw = CONTROL_TOKEN_RE.sub(" ", str(text or ""))
    return " ".join("".join(ch for ch in raw if ch in "\n\r\t" or ord(ch) >= 32).split()).strip()


CAMERA_DENIED = ("Kamera izni yok: System Settings › Privacy & Security › Camera bölümünde JARVIS 2'yi aç, "
                 "sonra kamerayı yeniden başlat.")


# gemini-3.1-flash-live-preview bağlantıyı konuşma olmasa da ~170 sn'de bir "1008 The operation was aborted" ile, önceden
# goAway göndermeden kesiyor (Google forumunda bildirilmiş, çözümü yok; 04.10 kayıt: 13 dk'da 5 kopma). JARVIS boştayken
# ~140 sn'de devam anahtarıyla kendisi yeniler; meşgulse 165 sn'ye kadar bekler, yine de koparsa hemen/sessizce bağlanır.
ROTATE_AFTER_S = 140.0
ROTATE_DEADLINE_S = 165.0


def is_server_recycle(exc) -> bool:
    text = str(exc).lower()
    return "1008" in text and "aborted" in text


def is_legacy_model(model: str) -> bool:
    """2.5 kuşağı (eski) sesli model mi? 3.x modelleri bazı ayarları farklı ister (05.10.2026, Google model sayfası)."""
    return "2.5" in str(model)


def thinking_config(model: str, budget: int) -> types.ThinkingConfig:
    """"Daha dikkatli düşün" ayarı: 2.5'te düşünme bütçesi, 3.x'te düşünme seviyesi (bütçe orada kullanılmıyor).
    Kapalıyken 3.x'te en düşük seviye (en hızlı cevap)."""
    if is_legacy_model(model):
        return types.ThinkingConfig(thinking_budget=budget)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM if budget > 0 else types.ThinkingLevel.MINIMAL)


async def send_text(session, text: str):
    """Konuşma sırasında yazılı mesaj (yazılan istek, sistem notu, kesintiden devam). gemini-3.1-flash-live-preview'de
    send_client_content yalnız oturum başında geçmiş yüklemek için kullanılabiliyor; konuşma ortasında gönderilince
    bağlantı "1008 The operation was aborted" ile kopuyordu (04.10 kayıt: 13 dk'da 5 kopma) → realtime input metni.
    Eski 2.5 modeli de bunu kabul ediyor (04.10'da gerçek anahtarla denendi)."""
    await session.send_realtime_input(text=str(text))


def api_key() -> str:
    return str(get_app_config_value("gemini_api_key", "") or "")


class Core:
    def __init__(self, state: State):
        self.state = state
        self.loop: asyncio.AbstractEventLoop | None = None
        self.session = None
        self.history = ConversationHistory()
        self.brain = BrainHost(state)
        self.phone = PhoneHost(state)
        self.webcam = WebcamStreamer()
        self.webcam.on_ended = lambda status: self._camera_ended()
        self.webcam.request_auth = self._request_camera_auth
        self.screen = ScreenWatcher()
        self.screen.on_ended = self._screen_ended
        self.audio_gate = AudioGate()          # aygıt değişince mikrofon + hoparlör birlikte yeniden açılır
        self.player = Player(self._on_output_level, self._on_player_speaking, self.audio_gate)
        self.player.blocked = lambda: self.state.muted or self.state.paused
        self.mic = Microphone(self._on_mic_frame, self.audio_gate)
        self.leveler = MicLeveler()
        self.stop_listener = StopCommandListener()
        self.unmute_audio = OfflineUtterances()       # mikrofon kapalıyken yalnız "mikrofon aç" aranır
        self.unmute_queue: asyncio.Queue | None = None
        # Gemini bağlantısı yokken: konuşma yerelde (Whisper) metne çevrilip temel komutlar yerelde çalışır.
        self.offline_audio = OfflineUtterances()
        self.offline_queue: asyncio.Queue | None = None
        self.offline_busy = False
        self.command_lock: asyncio.Lock | None = None
        self.unmute_generation = 0
        self.out_queue: asyncio.Queue | None = None
        self.mic_resume_at = 0.0
        self.resume_handle: str | None = None
        self.transcription_hints = True
        self.live_fallback = False             # yeni ses modeline bağlanılamadı → eski model
        self._live_failures = 0
        self.go_away = False
        self.response_pending = False
        self.tool_lock: asyncio.Lock | None = None
        self.pending_typed: list[str] = []
        self._typed_lock = threading.Lock()
        self._reconnect_evt: asyncio.Event | None = None
        self._reconnecting = False
        self.tasks = TaskHost(self)
        self.research = ResearchHost(state, self.inform, api_key=api_key, confirm=self.confirm)   # Chrome ajanı
        # Önizleme (JARVIS2_PREVIEW=1): Gemini'ye bağlanmaz, mikrofonu açmaz; yalnız arayüz denemesi için.
        self.preview = os.environ.get("JARVIS2_PREVIEW") == "1"

    # ── Arayüzden gelen istekler (Tk iş parçacığından) ────────────────────────────────────────
    def _call(self, coro):
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            coro.close()

    def submit_text(self, text: str):
        text = str(text or "").strip()
        if text and not self.state.paused:
            self._call(self._submit_text(text))

    def toggle_browser(self):
        """Küreye tıklama (araştırma sürerken ya da JARVIS Chrome'u açıkken): Chrome'u göster / gizle."""
        self._call(self.research.toggle_visible())

    def listen_now(self):
        self._call(self._interrupt("Dinliyorum; şimdi konuşabilirsin."))

    def request_reconnect(self):
        """Ses/anahtar/düşünme ayarı değişince yeni ayarlarla temiz bir oturum aç."""
        def go():
            self.resume_handle = None
            if self._reconnect_evt is not None:
                self._reconnect_evt.set()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(go)

    def _can_rotate(self) -> bool:
        """Oturum sessizce yenilenebilir mi: JARVIS konuşmuyor, cevap/araç beklenmiyor, kullanıcı son 5 sn konuşmadı."""
        s = self.state
        return (not s.speaking and not self.response_pending and not s.tool and self.player.queue.empty()
                and self.tasks.idle(need_session=True))

    async def _rotate_session(self, started: float):
        await asyncio.sleep(max(0.0, ROTATE_AFTER_S - (time.monotonic() - started)))
        while time.monotonic() - started < ROTATE_DEADLINE_S:
            if self._can_rotate():
                self.go_away = True            # _connect_loop: devam anahtarı korunur, 0,3 sn sonra yeniden bağlanır
                print("[BAĞLANTI] Oturum boştayken yenileniyor (yeni modelin ~3 dk kopmasından önce).", flush=True)
                raise ConnectionResetError("oturum yenileme")
            await asyncio.sleep(0.25)
        # Meşgul kaldı: kopma olursa is_server_recycle yolu hızlı ve sessiz yeniden bağlar.

    async def _wait_reconnect(self):
        await self._reconnect_evt.wait()
        self._reconnect_evt.clear()
        self._reconnecting = True
        raise ConnectionResetError("ayar değişti")

    def set_muted(self, muted: bool):
        self._call(self.apply_mute(muted))

    def set_paused(self, paused: bool):
        self.state.paused = paused
        if self.loop:
            self.loop.call_soon_threadsafe(self._reset_mic_buffers)
        if paused:
            self.player.interrupt()

    def set_webcam(self, active: bool, wait: float = 0.0) -> str:
        if not active:
            self.webcam.stop()
            self.state.webcam = False
            return "stopped"
        status = self.webcam.start(wait=wait)
        self.state.webcam = status in ("ok", "already_active")
        if status not in ("ok", "already_active"):
            self.state.log("err", {"camera_unavailable": "Kamera açılamadı.",
                                   "camera_denied": CAMERA_DENIED}.get(status,
                                                                       "Kamera için opencv-python yüklü değil."))
        return status

    def _request_camera_auth(self, index: int):
        """Kamera izni hiç sorulmamışsa arayüzden (ana iş parçacığı) macOS izin penceresini açtırır; cevabı bekler."""
        done, box = threading.Event(), {}

        def reply(status):
            box["status"] = status
            done.set()
        self.state.log("sys", "macOS kamera izni soruyor; açılan pencerede izin ver.")
        self.state.post("camera_auth", index, reply)
        done.wait(180)
        return box.get("status", 0)

    def toggle_webcam_async(self, active: bool):
        threading.Thread(target=self.set_webcam, args=(active, 5.0), daemon=True).start()

    def set_screen_watch(self, active: bool) -> str:
        if not active:
            self.screen.stop()
            self.state.screen_watch = False
            return "stopped"
        status = self.screen.start()
        self.state.screen_watch = status in ("ok", "already_active")
        return status

    def toggle_screen_watch_async(self, active: bool):
        def run():
            status = self.set_screen_watch(active)
            if active:
                self.state.log("sys" if status in ("ok", "already_active") else "err", self.screen_watch_message(status))
            else:
                self.state.log("sys", "Ekran izleme kapatıldı.")
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def screen_watch_message(status: str) -> str:
        return {
            "ok": "Ekranı izliyorum. Artık ekranında olanı canlı görüyorum; 15 dakika sonra kendiliğinden kapanır.",
            "already_active": "Ekranı zaten izliyorum; süreyi yeniden 15 dakikaya uzattım.",
            "permission_needed": ("Ekranı izleyemiyorum: JARVIS 2'ye ekran kaydı izni gerekiyor. System Settings › "
                                  "Privacy & Security › Screen & System Audio Recording bölümünde JARVIS 2'yi aç, "
                                  "sonra JARVIS 2'yi yeniden başlat."),
            "capture_failed": "Ekran izleme başlatılamadı: ekran görüntüsü alınamadı.",
        }.get(status, "Ekran izleme başlatılamadı.")

    def _camera_ended(self):
        self.state.webcam = False
        self.state.log("err", "Kamera görüntüsü kesildi; kamera kapatıldı.")

    def _screen_ended(self, reason: str):
        self.state.screen_watch = False
        self.state.log("sys", "Ekran izleme 15 dakika dolduğu için kapandı." if reason == "timeout"
                       else "Ekran görüntüsü alınamadı; ekran izleme kapatıldı.")

    async def inform(self, text: str, spoken: str = ""):
        """Arka plandaki bir iş (Chrome araştırması) bitince JARVIS'e haber verir: Gemini bağlıysa sistem notu olarak
        gönderilir ve model kendi sesiyle anlatır; bağlı değilse kısa özet macOS sesiyle okunur. Kullanıcı konuşurken ya
        da JARVIS cevap verirken araya girmemek için en çok ~60 sn boşta olması beklenir."""
        for _ in range(120):
            if self.tasks.idle(need_session=self.session is not None):
                break
            await asyncio.sleep(0.5)
        if self.session is not None and not self.state.paused:
            self.player.discard = False
            self.response_pending = True
            try:
                await send_text(self.session, text)
                return
            except Exception:
                self.response_pending = False
        if spoken:
            await self.tasks.announce(spoken, spoken=True, explicit=True)

    async def confirm(self, title: str, text: str, timeout: float = 120.0) -> bool:
        """Kullanıcıdan pencereyle evet/hayır onayı alır (arayüz iş parçacığında sorulur)."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def answer(ok):
            loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(bool(ok)))
        self.state.post("confirm", title, text, answer)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return False

    def shutdown(self):
        self.research.shutdown()
        self.tasks.shutdown()
        self.phone.shutdown()
        self.webcam.stop()
        try:
            self.screen.stop()
        except Exception:
            pass
        self.brain.shutdown()

    # ── Mikrofon ─────────────────────────────────────────────────────────────────────────────
    def _blocked(self) -> bool:
        s = self.state
        return s.muted or s.paused or s.speaking or time.monotonic() < self.mic_resume_at

    def _on_mic_frame(self, data: bytes, rate: int, overflow: bool):
        s = self.state
        rms = rms_int16(data)
        s.mic_level = min(1.0, rms / 3000.0)
        s.mic_mode = ("KAPALI · YALNIZ \"MİKROFON AÇ\" DİNLENİYOR" if s.muted else "DURAKLATILDI" if s.paused
                      else "KONUŞUYOR · \"JARVIS SUS\" YA DA F7 İLE KES" if s.speaking
                      else "İNTERNETSİZ · TEMEL KOMUTLAR" if self.session is None else "DİNLİYOR")
        blocked = self._blocked()
        if rms > 80 and not blocked:
            s.mark_user_activity()
        if s.muted and not s.paused and not s.speaking and time.monotonic() >= self.mic_resume_at:
            utt = self.unmute_audio.feed(data, rate, rms)
            if utt is not None and self.unmute_queue is not None and not self.unmute_queue.full():
                self.unmute_queue.put_nowait((*utt, self.unmute_generation))
        else:
            self.unmute_audio.reset()
        if blocked:
            self.offline_audio.reset()
            if s.speaking and not s.muted and not s.paused:
                self.stop_listener.feed(data, rate)
            else:
                self.stop_listener.reset()
            return
        if self.session is None:
            if self.preview or self.offline_queue is None:
                return
            if self.offline_busy:
                self.offline_audio.reset()
                return
            utt = self.offline_audio.feed(data, rate, rms)
            if utt is not None and not self.offline_queue.full():
                self.offline_busy = True
                self.offline_queue.put_nowait(utt)
            return
        data = self.leveler.process(data, rms)
        if self.out_queue.full():
            self.out_queue.get_nowait()          # ağ yavaşsa en eski parçayı bırak; sürücüyü bekletme
        self.out_queue.put_nowait({"data": data, "mime_type": f"audio/pcm;rate={rate}"})

    def _reset_mic_buffers(self):
        self.unmute_generation += 1
        self.unmute_audio.reset()
        self.offline_audio.reset()
        self.stop_listener.reset()
        for q in (self.unmute_queue, self.out_queue, self.offline_queue):
            if q is not None:
                while not q.empty():
                    q.get_nowait()
                    if q is self.offline_queue:
                        self.offline_busy = False    # işlenmeden silinen cümle "meşgul" bırakmasın

    async def apply_mute(self, muted: bool):
        if self.state.muted == muted:
            return
        self.state.muted = muted
        self._reset_mic_buffers()
        self.mic_resume_at = time.monotonic() + .35
        if muted:
            self.player.discard = True
            self.player.interrupt()
        self.state.log("sys", "Mikrofon kapatıldı. Açmak için \"mikrofon aç\" de ya da F4'e bas." if muted
                       else "Mikrofon açıldı; dinliyorum.")
        self.state.post("mute", muted)

    async def _mic_command(self, text: str) -> bool:
        """"mikrofon kapat/aç" yerelde işlenir (buluta gitmeden)."""
        muted = microphone_command(text)
        if muted is None or self.state.paused:
            return False
        if self.state.muted != muted:
            self.state.heard = text
            await self.apply_mute(muted)
        return True

    async def _unmute_listener(self):
        while True:
            data, rate, generation = await self.unmute_queue.get()
            if not self.state.muted or self.state.paused or generation != self.unmute_generation:
                continue
            try:
                await asyncio.to_thread(self.stop_listener.load)
                text = await asyncio.to_thread(self.stop_listener.recognize, data, rate,
                                               "Mikrofon açıl. Mikrofon aç. Jarvis.")
                if self.state.muted and generation == self.unmute_generation:
                    await self._mic_command(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[MİKROFON] Yerel komut: {type(exc).__name__}", flush=True)

    async def _stop_word_listener(self):
        s = self.state
        await self.stop_listener.run(
            lambda: s.speaking and not s.muted and not s.paused,
            lambda: self._interrupt("Konuşma durduruldu."),
            lambda error: s.log("err", error + " F7 ile de durdurabilirsin."),
            on_ready=lambda: print("[SES] Yerel komutlar hazır: Jarvis sus / mikrofon aç / mikrofon kapat", flush=True),
            on_command=self._mic_command)

    # ── Hoparlör ─────────────────────────────────────────────────────────────────────────────
    def _on_output_level(self, level: float):
        self.state.output_level = level

    def _on_player_speaking(self, value: bool):
        s = self.state
        if value == s.speaking:
            return
        self.stop_listener.reset()
        if value:
            if self.out_queue is not None:
                while not self.out_queue.empty():
                    self.out_queue.get_nowait()
            s.set_status("SPEAKING")
        else:
            self.mic_resume_at = max(self.mic_resume_at, time.monotonic() + 0.18)
            s.output_level = 0.0
            s.set_status("LISTENING")

    async def _interrupt(self, message: str = ""):
        self.player.discard = True
        self.player.interrupt()
        self.tasks.stop_speech()
        if message:
            self.state.log("sys", message)

    # ── Gemini oturumu ───────────────────────────────────────────────────────────────────────
    def _config(self, model: str = DEFAULT_MODEL) -> types.LiveConnectConfig:
        from memory.memory_manager import format_memory_for_prompt, load_memory
        try:
            mem = format_memory_for_prompt(load_memory())
        except Exception:
            mem = ""
        try:
            hist = self.history.recent_context(max_chars=5000)
        except Exception:
            hist = ""
        extra = {}
        if hasattr(types, "ContextWindowCompressionConfig"):
            extra["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow())
        if hasattr(types, "SessionResumptionConfig"):
            extra["session_resumption"] = types.SessionResumptionConfig(handle=self.resume_handle)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription=(
                types.AudioTranscriptionConfig(language_codes=TRANSCRIPTION_LANGUAGES,
                                               custom_vocabulary=TRANSCRIPTION_VOCABULARY)
                if self.transcription_hints else {}),
            thinking_config=thinking_config(model, int(get_app_config_value("thinking_budget", 0) or 0)),
            realtime_input_config=types.RealtimeInputConfig(
                **({} if is_legacy_model(model) else {"turn_coverage": types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY}),
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=20,
                    silence_duration_ms=700)),
            system_instruction=prompt.build(tools.available(), mem, hist),
            tools=[{"function_declarations": tools.declarations()}],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=str(get_app_config_value("voice", "Charon") or "Charon")))),
            **extra)

    async def _submit_text(self, text: str):
        if await self._mic_command(text):
            return
        if self.session is None:
            if self.preview:
                self.state.log("you", text)
                self.state.log("sys", "Önizleme: komut çalıştırılmadı.")
            else:
                await self.local_command(text)
            return
        self.state.log("you", text)
        with self._typed_lock:
            self.pending_typed.append(text)
        self.tasks.typed_request(text)
        self.player.discard = False
        self.response_pending = True
        try:
            await send_text(self.session, text)
        except Exception as exc:
            self.response_pending = False
            self.state.log("err", f"İstek gönderilemedi: {type(exc).__name__}")

    # ── İnternetsiz temel komutlar (Gemini bağlı değilken) ───────────────────────────────────
    async def _offline_commands(self):
        """Bağlantı yokken biten her cümle yerel Whisper ile metne çevrilir ve local_command'a verilir."""
        s = self.state
        while True:
            data, rate = await self.offline_queue.get()
            try:
                if s.muted or s.paused or self.session is not None:
                    continue
                generation = self.unmute_generation
                s.set_status("THINKING")
                await asyncio.to_thread(self.stop_listener.load)
                text = await asyncio.to_thread(self.stop_listener.recognize, data, rate, OFFLINE_PROMPT)
                if generation != self.unmute_generation or s.muted or s.paused:
                    continue
                if text:
                    await self.local_command(text)
                else:
                    s.log("sys", "Söylediğini net anlayamadım; tekrar söyleyebilirsin.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                s.log("err", "İnternetsiz ses tanıma çalışamadı: " + type(exc).__name__)
            finally:
                self.offline_busy = False
                if s.status == "THINKING" and not s.tool:
                    s.set_status("LISTENING")

    async def local_command(self, text: str):
        """Kalıp komutlar (actions/offline_commands.py, asıl JARVIS'le aynı): uygulama aç, ses, pil/saat, dosya
        bul/taşı/adını değiştir/çöpe at (tek kesin eşleşme şart), geri al, görev durumu. Cevap macOS sesiyle okunur."""
        if await self._mic_command(text):
            return
        s = self.state
        async with self.command_lock:
            self.offline_busy = True
            s.heard = text
            s.log("you", text)
            h, key = self.tasks, None
            try:
                intent = parse_command(text)
                if "file_action" in intent:
                    intent = await asyncio.to_thread(resolve_file_command, intent)
                if "tool" in intent:
                    name, args = intent["tool"], intent["args"]
                    tool_id = None
                    if name not in ("get_parallel_tasks", "resume_tasks", "get_action_history"):
                        key = h.requests.begin(text)
                        tool_id, _ = h.requests.before(key, name, args)
                    result = await self.run_tool(name, args)
                    raw = tools.as_text(result)
                    if key:
                        h.requests.after(key, tool_id, raw, tools.is_error(result, name))
                        h.requests.complete(key)
                    answer = spoken_result(raw)
                else:
                    answer = intent["message"]
                s.log("ai", answer)
                await self._record_turn(text, answer)
                await self.tasks.speak(answer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                traceback.print_exc()
                s.log("err", "Yerel komut tamamlanamadı: " + str(exc)[:160])
            finally:
                self.offline_audio.reset()
                self.offline_busy = False
                if s.status == "THINKING" and not s.tool:
                    s.set_status("LISTENING")

    async def _send_audio(self):
        while True:
            msg = await self.out_queue.get()
            if self._blocked():
                continue
            await self.session.send_realtime_input(audio=types.Blob(**msg))

    async def _send_frames(self, source, active):
        """Kamera ya da ekran açıkken en güncel kareyi ~1,5 sn'de bir oturuma gönderir."""
        last = None
        while True:
            if not active() or self.state.paused:
                await asyncio.sleep(0.3)
                continue
            jpeg = source()
            if jpeg is None or jpeg is last:
                await asyncio.sleep(0.25)
                continue
            last = jpeg
            try:
                # "media" (media_chunks) eskidi: gemini-3.1-flash-live-preview onu 1007 hatasıyla reddedip bağlantıyı
                # kesiyordu (04.10 kayıt; kamera/ekran açılınca bağlantı gidip geldi) → "video" alanı.
                await self.session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
            except Exception as exc:
                print(f"[GÖRÜNTÜ] Kare gönderilemedi: {exc}", flush=True)
            await asyncio.sleep(1.5)

    async def _receive(self):
        s = self.state
        out_buf, in_buf = [], []
        control_turn = False
        while True:
            async for msg in self.session.receive():
                upd = getattr(msg, "session_resumption_update", None)
                if upd is not None and getattr(upd, "resumable", False) and getattr(upd, "new_handle", None):
                    self.resume_handle = upd.new_handle
                if getattr(msg, "go_away", None):
                    self.go_away = True
                if msg.data and not self.player.discard and not s.muted and not s.paused:
                    self.player.queue.put_nowait(msg.data)
                sc = msg.server_content
                if sc:
                    if sc.interrupted:
                        self.player.interrupt()
                    if sc.output_transcription and sc.output_transcription.text and not control_turn:
                        txt = clean_output(sc.output_transcription.text)
                        if txt:
                            out_buf.append(txt)
                    raw_in = (sc.input_transcription.text or "") if sc.input_transcription else ""
                    in_text = INPUT_TAG_RE.sub(" ", raw_in).strip()
                    accept = not s.muted and not s.paused
                    if accept and raw_in:
                        in_buf.append(raw_in)
                    finished = bool(sc.input_transcription and sc.input_transcription.finished)
                    sentence_end = in_text.endswith((".", "!"))
                    if (accept and in_buf and (finished or sentence_end or sc.turn_complete)
                            and await self._mic_command(join_transcript(in_buf))):
                        control_turn = True
                        await self._interrupt()
                        self.tasks.control_turn()
                        self.response_pending = False
                        in_buf, out_buf = [], []
                    elif accept and in_text:
                        control_turn = False
                        self.player.discard = False
                        self.response_pending = True
                        s.heard = join_transcript(in_buf)
                        self.tasks.user_text(s.heard)
                        s.mark_user_activity()
                    if sc.turn_complete:
                        self.response_pending = False
                        self.tasks.turn_done()
                        self.player.queue.put_nowait(None)
                        full_in = join_transcript(in_buf)
                        if full_in:
                            s.log("you", full_in)
                        full_out = " ".join(out_buf).strip()
                        if full_out:
                            s.log("ai", full_out)
                        if not s.speaking and s.status in ("THINKING", "INITIALISING"):
                            s.set_status("LISTENING")
                        await self._record_turn(full_in, full_out)
                        in_buf, out_buf = [], []
                if msg.tool_call:
                    self.response_pending = True
                    calls = list(msg.tool_call.function_calls or [])
                    asyncio.create_task(self._answer_tools(calls, control_turn))
                if sc and sc.turn_complete:
                    control_turn = False

    async def _record_turn(self, spoken: str, answer: str):
        with self._typed_lock:
            typed, self.pending_typed = self.pending_typed, []
        try:
            await asyncio.to_thread(self.history.record_turn, typed, spoken, answer)
        except Exception:
            print("[GEÇMİŞ] Konuşma kaydedilemedi.", flush=True)

    async def _answer_tools(self, calls, control_turn: bool):
        responses = []
        for fc in calls:
            if control_turn:
                result = "Mikrofon komutu yerel olarak işlendi; ek araç işlemi yapılmadı."
            else:
                result = await self.tasks.run_checked(fc.name, dict(fc.args or {}))
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name,
                                                    response={"result": tools.as_text(result)}))
        session = self.session
        if session is not None:
            try:
                await session.send_tool_response(function_responses=responses)
            except Exception as exc:
                print(f"[ARAÇ] Sonuç gönderilemedi: {exc}", flush=True)

    async def run_tool(self, name: str, args: dict) -> object:
        """Bir aracı çalıştırır; durum, etkinlik paneli, işlem geçmişi ve konuşma geçmişi burada güncellenir.
        Araçlar sırayla çalışır (aynı anda iki dosya işlemi yarışmasın); iş akışı ve paralel görevler kilidi
        dışarıdan tutmaz (iş akışı her adımda kendisi alır)."""
        s = self.state
        print(f"[ARAÇ] {name} {args}", flush=True)
        s.set_status("THINKING")
        s.tool_started(name, tools.label(name))
        failed = False
        async with (contextlib.nullcontext() if name in UNLOCKED_TOOLS else self.tool_lock):
            try:
                result = await tools.run(name, args, self)
            except Exception as exc:
                traceback.print_exc()
                result, failed = f"Hata: {exc}", True
                s.log("err", f"{tools.label(name)}: {str(exc)[:120]}")
        failed = failed or tools.is_error(result, name)
        s.tool_finished(name, not failed)
        try:
            from actions.action_history import OTHER_CHANGES, record_other_change
            if name in OTHER_CHANGES:
                await asyncio.to_thread(record_other_change, name, args, failed)
        except Exception:
            pass
        if name in HISTORY_REFRESH_TOOLS or name in ("undo_action",):
            s.post("refresh_action_history")
        if not failed and name in SUCCESS_SFX_TOOLS:
            s.post("sfx", "success")
        elif not failed and name == "send_whatsapp_message" and args.get("send_now") \
                and "gönderildi" in str(result).lower():
            s.post("sfx", "success")
        if name in TOOL_NAMES:
            try:
                await asyncio.to_thread(self.history.record_tool, name, args, str(tools.as_text(result)))
            except Exception:
                pass
        if failed:
            s.set_status("ERROR")
            s.error_hold_until = time.time() + 4.0
        elif not s.speaking and not s.muted:
            s.set_status("LISTENING")
        print(f"[ARAÇ] {name} → {str(result)[:100]}", flush=True)
        return result

    async def _connect_loop(self):
        s = self.state
        notified = False
        while True:
            if s.paused or not api_key() or self.preview:
                s.cloud = False
                await asyncio.sleep(2)
                continue
            client, connected = None, False
            try:
                client = genai.Client(api_key=api_key(), http_options={"api_version": "v1alpha"})
                model = self._live_model()
                started = time.monotonic()
                async with client.aio.live.connect(model=model, config=self._config(model)) as session:
                    # Yarım kalan internetsiz komut bitmeden konuşma buluta devredilmez.
                    wait_until = time.monotonic() + 20
                    while ((self.offline_busy or self.offline_audio.active or self.tasks.speaking_locally)
                           and time.monotonic() < wait_until):
                        await asyncio.sleep(.1)
                    self.offline_audio.reset()
                    connected = True
                    resumed = self.resume_handle is not None
                    self.session = session
                    self.go_away = False
                    self.response_pending = False
                    self.player.discard = False
                    self.tasks.session_started()
                    s.cloud = True
                    while not self.out_queue.empty():
                        self.out_queue.get_nowait()
                    if s.status in ("INITIALISING", "ERROR") and not s.speaking:
                        s.set_status("LISTENING")
                    if not notified or not resumed:
                        s.log("sys", "Gemini bağlantısı hazır. Dinliyorum…" if not resumed
                              else "Bağlantı yenilendi; konuşma kaldığı yerden sürüyor.")
                    notified = True
                    print(f"[BAĞLANTI] Model: {model}", flush=True)
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._send_audio())
                        tg.create_task(self._receive())
                        tg.create_task(self._send_frames(self.webcam.get_latest_frame, lambda: self.webcam.is_active))
                        tg.create_task(self._send_frames(self.screen.get_latest_frame, lambda: self.screen.is_active))
                        tg.create_task(self._wait_reconnect())
                        if not is_legacy_model(model):
                            tg.create_task(self._rotate_session(started))
                        tg.create_task(self.tasks.resume_request())
                        tg.create_task(self.tasks.watch_request_resume())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
                    exc = exc.exceptions[0]           # TaskGroup sarmalı: asıl hatayı kayda yaz
                if self._note_live_failure(model, not connected or time.monotonic() - started < 3):
                    s.log("sys", "Yeni ses modeline bağlanılamadı; eski modelle devam ediliyor.")
                detail = str(exc).lower().replace("_", "")
                if self.transcription_hints and not connected and any(w in detail for w in (
                        "languagecodes", "customvocabulary", "inputaudiotranscription", "unknown name", "1007")):
                    self.transcription_hints = False
                    print("[BAĞLANTI] Döküm ipuçları reddedildi; ipuçsuz bağlanılıyor: " + str(exc)[:200], flush=True)
                elif self.resume_handle and not connected:
                    self.resume_handle = None          # süresi dolmuş devam anahtarı: temiz oturum aç
                    print("[BAĞLANTI] Oturum devamı reddedildi; yeni oturum açılıyor.", flush=True)
                elif self._reconnecting:
                    self._reconnecting = False
                    print("[BAĞLANTI] Yeni ayarlarla yeniden bağlanılıyor.", flush=True)
                elif self.go_away:
                    print("[BAĞLANTI] Sunucu oturumu yeniledi; yeniden bağlanılıyor.", flush=True)
                elif connected and is_server_recycle(exc):
                    self.go_away = True                # devam anahtarıyla 0,3 sn'de, sohbete yazmadan yeniden bağlan
                    print("[BAĞLANTI] Yeni model oturumu kapattı (bilinen ~3 dk sorunu); hemen yeniden bağlanılıyor.",
                          flush=True)
                else:
                    print(f"[BAĞLANTI] {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                    if notified or not connected:
                        s.log("sys", "Gemini bağlantısı koptu; arka planda yeniden deneniyor. Bu arada internetsiz "
                                     "temel komutlar açık (uygulama aç, ses, pil/saat, dosya bul/taşı, geri al).")
                        notified = False
            finally:
                self.session = None
                s.cloud = False
                self.response_pending = False
                self.player.interrupt()
                if client is not None:
                    try:
                        await client.aio.aclose()
                    except Exception:
                        pass
            await asyncio.sleep(0.3 if (self.go_away or self.resume_handle is None and connected) else 3)

    def _live_model(self) -> str:
        configured = str(get_app_config_value("live_model", "") or "").strip()
        if configured:
            return configured
        return FALLBACK_MODEL if self.live_fallback else DEFAULT_MODEL

    def _note_live_failure(self, model: str, short: bool) -> bool:
        """Yeni (önizleme) model art arda 2 kez bağlanamaz ya da 3 sn içinde kapanırsa eski modele geçer. True = geçildi."""
        if model != DEFAULT_MODEL or self.live_fallback:
            return False
        if not short:
            self._live_failures = 0
            return False
        self._live_failures += 1
        if self._live_failures < 2:
            return False
        self.live_fallback = True
        self.resume_handle = None              # eski modelin devam anahtarı yeni modelde geçmez (ve tersi)
        print(f"[BAĞLANTI] {DEFAULT_MODEL} bağlanamadı; {FALLBACK_MODEL} kullanılıyor.", flush=True)
        return True

    async def _watch_audio_devices(self):
        """macOS'ta varsayılan mikrofon/hoparlör değişince (AirPods takıldı, kulaklık çıkarıldı…) ikisini de yeni
        aygıtla yeniden açar. Değişiklik ~1 sn sabit kalınca uygulanır (bağlanırken aygıt bir an gidip gelebilir)."""
        from jarvis import audio_devices
        last = await asyncio.to_thread(audio_devices.default_devices)
        pending, since = None, 0.0
        while True:
            await asyncio.sleep(1.0)
            now = await asyncio.to_thread(audio_devices.default_devices)
            if None in now or now == last:
                pending = None
                continue
            if now != pending:
                pending, since = now, time.monotonic()
                continue
            if time.monotonic() - since < 0.9:
                continue
            parts = []
            for i, label in ((0, "mikrofon"), (1, "hoparlör")):
                if now[i] != last[i]:
                    name = await asyncio.to_thread(audio_devices.device_name, now[i])
                    parts.append(f"{label}: {name or 'yeni aygıt'}")
            last, pending = now, None
            print("[SES] Aygıt değişti → " + ", ".join(parts), flush=True)
            ok = await self.audio_gate.restart(self.mic, self.player)
            self.state.log("sys", "Ses aygıtı değişti (" + ", ".join(parts) + "). "
                           + ("Yeni aygıta geçtim." if ok else "Yeni aygıta geçiliyor…"))

    async def _service(self, fn, name: str):
        """Yerel hizmetler çökerse kayda yazılıp 3 sn sonra yeniden başlar (JARVIS'in tamamı durmaz)."""
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[{name}] {type(exc).__name__}: {exc}", flush=True)
                if name == "MİKROFON":
                    self.state.mic_mode = "MİKROFON HATASI"
                    self.state.mic_level = 0.0
                    if str(exc) != getattr(self, "_last_mic_err", ""):
                        self.state.log("err", str(exc))
                        self._last_mic_err = str(exc)
                await asyncio.sleep(3)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.out_queue = asyncio.Queue(maxsize=32)
        self.unmute_queue = asyncio.Queue(maxsize=1)
        self.offline_queue = asyncio.Queue(maxsize=1)
        self.command_lock = asyncio.Lock()
        self.tool_lock = asyncio.Lock()
        self._reconnect_evt = asyncio.Event()

        def mic_ready(name, rate):
            self.state.mic_device = name
            self._last_mic_err = ""
            print(f"[MİKROFON] {name} ({rate} Hz)", flush=True)

        if self.preview:
            self.state.log("sys", "Önizleme modu: Gemini'ye bağlanılmıyor, mikrofon kapalı.")
            await asyncio.Event().wait()
        if not api_key():
            self.state.log("sys", "Gemini API anahtarı yok: Ayarlar › ZEKÂ › Gemini API anahtarı. "
                                  "O zamana kadar internetsiz temel komutlar açık.")
        self.tasks.startup_notice()
        async with asyncio.TaskGroup() as tg:
            for name, service in self.tasks.services():
                tg.create_task(self._service(service, name))
            tg.create_task(self._service(lambda: self.mic.run(mic_ready), "MİKROFON"))
            tg.create_task(self._service(self.player.run, "HOPARLÖR"))
            tg.create_task(self._service(self._stop_word_listener, "YEREL SES"))
            tg.create_task(self._service(self._unmute_listener, "MİKROFON AÇ"))
            tg.create_task(self._service(self._watch_audio_devices, "SES AYGITI"))
            tg.create_task(self._service(self._offline_commands, "İNTERNETSİZ KOMUT"))
            tg.create_task(self._connect_loop())
            tg.create_task(self._service(self._control_channel, "DENETİM KANALI"))

    async def _control_channel(self):
        """Telefon ajanının masaüstü araçlarını (Chrome araştırması) çalıştırdığı yerel kanal (jarvis/control.py)."""
        from jarvis import control
        await control.serve(self)


def start_in_thread(core: Core) -> threading.Thread:
    def runner():
        try:
            asyncio.run(core.run())
        except Exception as exc:
            traceback.print_exc()
            core.state.log("err", f"JARVIS çekirdeği durdu: {type(exc).__name__}: {exc}. Yeniden başlat.")
    t = threading.Thread(target=runner, name="jarvis-core", daemon=True)
    t.start()
    return t
