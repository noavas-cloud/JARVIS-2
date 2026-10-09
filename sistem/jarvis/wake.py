""""Hey Jarvis": JARVIS 2 kapalıyken arka planda dinler, uyandırma sözünü duyunca JARVIS 2'yi açar.

Varsayılan kapalı; Ayarlar › SES KOMUTLARI'ndan açılır. Açılınca ~/Library/LaunchAgents/com.kemal.jarvis2.wake.plist
kurulur ve launchd dinleyiciyi oturum boyunca çalışır tutar. Ses tamamen yerelde (Whisper "base", internet gerekmez)
işlenir; hiçbir kayıt diske yazılmaz ve hiçbir yere gönderilmez.

Dinlemez (mikrofonu bırakır):
  • JARVIS 2 açıkken (kendi kilidi; JARVIS 2 mikrofonu kendisi kullanır),
  • asıl JARVIS açıkken (iki JARVIS aynı anda açılmasın),
  • asıl JARVIS'in uyandırma servisi kuruluysa (aynı söz iki uygulamayı birden açmasın).

Algılama/tanıma asıl JARVIS'in wake_service.py'sinin aynısı ("hey jarvis" ya da "jarvis açıl").
Çalıştırma: python -m jarvis.wake            (launchd bunu çalıştırır)
            python -m jarvis.wake install|uninstall|status
"""

from __future__ import annotations

import array
import math
import os
import plistlib
import re
import subprocess
import sys
import time
import unicodedata
from collections import deque
from pathlib import Path

from jarvis.paths import BASE_DIR, BUNDLE_ID, LOG_DIR, RUNTIME_DIR

LABEL = BUNDLE_ID + ".wake"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
ORIGINAL_WAKE_PLIST = AGENTS_DIR / "com.jarvis.wake.plist"     # asıl JARVIS'in servisi (yalnız varlığına bakılır)
MODEL_DIR = BASE_DIR / "models"
DOMAIN = f"gui/{os.getuid()}"

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480            # 30 ms
PRE_ROLL_FRAMES = 10
END_SILENCE_FRAMES = 20
MAX_PHRASE_FRAMES = 150


# ── Uyandırma sözü (asıl JARVIS wake_service.py ile aynı) ─────────────────────────────────────
def _normalized(text: str) -> str:
    text = text.casefold().replace("ı", "i")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def is_wake_phrase(text: str) -> bool:
    words = _normalized(text).split()
    return any(words[i:i + 2] in (["hey", "jarvis"], ["jarvis", "acil"]) for i in range(len(words) - 1))


def _rms(frame: bytes) -> float:
    values = array.array("h")
    values.frombytes(frame)
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


class PhraseCollector:
    def __init__(self):
        self.preroll = deque(maxlen=PRE_ROLL_FRAMES)
        self.frames: list[bytes] = []
        self.levels: list[float] = []
        self.silence = 0
        self.voiced = 0
        self.noise_floor = 80.0

    def feed(self, frame: bytes) -> bytes | None:
        level = _rms(frame)
        voiced = level > max(180.0, self.noise_floor * 2.8)
        if not self.frames:
            if not voiced:
                self.noise_floor = self.noise_floor * .98 + min(level, 1500) * .02
                self.preroll.append(frame)
                return None
            self.frames = list(self.preroll) + [frame]
            self.levels = [level]
            self.preroll.clear()
            self.silence, self.voiced = 0, 1
            return None
        self.frames.append(frame)
        self.levels.append(level)
        self.voiced += int(voiced)
        self.silence = 0 if voiced else self.silence + 1
        if self.silence < END_SILENCE_FRAMES and len(self.frames) < MAX_PHRASE_FRAMES:
            return None
        if self.silence < END_SILENCE_FRAMES:
            # Sessizlik hiç gelmedi (fan, müzik): gürültü tabanını ona uydur, Whisper sürekli çalışmasın.
            ordered = sorted(self.levels)
            self.noise_floor = max(self.noise_floor, min(ordered[len(ordered) // 2], 3000.0) * .9)
        result, voiced_frames = b"".join(self.frames), self.voiced
        self.frames, self.levels, self.silence, self.voiced = [], [], 0, 0
        return result if voiced_frames >= 8 else None


class WakeRecognizer:
    def __init__(self):
        from faster_whisper import WhisperModel
        self.model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=2,
                                  download_root=str(MODEL_DIR), local_files_only=True)

    def recognize(self, pcm: bytes) -> str:
        import numpy as np
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(samples, language="tr", beam_size=2, temperature=0,
                                            condition_on_previous_text=False, hotwords="Hey Jarvis, Jarvis açıl",
                                            vad_filter=False)
        return " ".join(s.text for s in segments if s.no_speech_prob < .6 and s.avg_logprob > -1.2).strip()


# ── Ne zaman dinlenmez ─────────────────────────────────────────────────────────────────────────
def jarvis2_running(runtime_dir: Path = RUNTIME_DIR) -> bool:
    """JARVIS 2 açık mı: kilit dosyasındaki süreç numarası yaşıyor ve "jarvis.app" çalıştırıyor mu.
    Kilidin kendisi denenmez: tam o anda açılan JARVIS 2 kilidi alamayıp kendini "zaten açık" sanmasın."""
    try:
        pid = int((Path(runtime_dir) / "main.lock").read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        import psutil
        return "jarvis.app" in " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return False


_ORIG = {"at": -1e9, "running": False}


def blocked_reason() -> str:
    """Boş metin = dinlenebilir."""
    if jarvis2_running():
        return "JARVIS 2 açık"
    if ORIGINAL_WAKE_PLIST.exists():
        return "asıl JARVIS'in uyandırma servisi kurulu"
    now = time.monotonic()
    if now - _ORIG["at"] > 10:            # süreç listesini taramak pahalı: 10 sn'de bir
        from jarvis.app import original_running
        _ORIG.update(at=now, running=original_running())
    return "asıl JARVIS açık" if _ORIG["running"] else ""


def _app() -> Path:
    from jarvis.app_bundle import app_path
    return app_path()


def _open_jarvis2() -> bool:
    result = subprocess.run(["/usr/bin/open", str(_app())], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print("JARVIS 2 açılamadı: " + result.stderr.strip(), file=sys.stderr, flush=True)
        return False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if jarvis2_running():
            return True
        time.sleep(.2)
    return False


def _listen(recognizer: WakeRecognizer) -> bool:
    import pyaudio
    audio = pyaudio.PyAudio()
    stream = None
    try:
        stream = audio.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, input=True,
                            frames_per_buffer=FRAME_SAMPLES)
        collector = PhraseCollector()
        print("Hazır: 'hey jarvis' veya 'jarvis açıl' bekleniyor.", flush=True)
        next_check = 0.0
        while True:
            if time.monotonic() >= next_check:
                if blocked_reason():
                    return False
                next_check = time.monotonic() + .5
            phrase = collector.feed(stream.read(FRAME_SAMPLES, exception_on_overflow=False))
            if phrase is None:
                continue
            text = recognizer.recognize(phrase)
            if is_wake_phrase(text):
                print("Uyandırma sözü algılandı: " + text, flush=True)
                return True
    finally:
        if stream is not None:
            stream.close()
        audio.terminate()


def main() -> None:
    while True:
        try:
            recognizer = WakeRecognizer()
            break
        except Exception as exc:
            # Model yoksa çökme: launchd her 10 sn'de yeniden başlatıp CPU harcamasın.
            print(f"Uyandırma modeli yüklenemedi ({type(exc).__name__}: {exc}); 10 dk sonra tekrar.",
                  file=sys.stderr, flush=True)
            time.sleep(600)
    last_reason = None
    while True:
        reason = blocked_reason()
        if reason:
            if reason != last_reason:
                print(f"Bekliyor: {reason}.", flush=True)
                last_reason = reason
            time.sleep(1)
            continue
        last_reason = None
        try:
            if _listen(recognizer) and not blocked_reason():
                _open_jarvis2()
                time.sleep(5)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"Dinleyici yeniden deneyecek: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)


# ── Kurulum (Ayarlar'daki anahtar) ─────────────────────────────────────────────────────────────
def plist_path(agents_dir: Path | None = None) -> Path:
    return Path(agents_dir or AGENTS_DIR) / f"{LABEL}.plist"


def _python() -> tuple[str, dict]:
    """Uygulama içindeki Python (mikrofon izni penceresinde "jarvis-python" adıyla görünür); çalışmazsa venv."""
    venv = BASE_DIR / "venv" / "bin" / "python"
    bundled = _app() / "Contents" / "MacOS" / "jarvis-python"
    env = {"__PYVENV_LAUNCHER__": str(venv)}
    if bundled.exists():
        try:
            ok = subprocess.run([str(bundled), "-c", "import faster_whisper, pyaudio"], env={**os.environ, **env},
                                capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            return str(bundled), env
    return str(venv), {}


def definition(python: str | None = None, extra_env: dict | None = None) -> dict:
    if python is None:
        python, extra_env = _python()
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-u", "-m", "jarvis.wake"],
        "WorkingDirectory": str(BASE_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_DIR / "wake.log"),
        "StandardErrorPath": str(LOG_DIR / "wake.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                 "HOME": str(Path.home()), "PYTHONUNBUFFERED": "1", **(extra_env or {})},
    }


def install_problem() -> str:
    if ORIGINAL_WAKE_PLIST.exists():
        return ("Asıl JARVIS'in \"Hey Jarvis\" servisi kurulu; ikisi aynı sözle birlikte açılırdı. "
                "Önce asıl JARVIS'te uyandırmayı kapat.")
    if not any(MODEL_DIR.glob("models--Systran--faster-whisper-base")):
        return "Yerel ses modeli (Whisper base) bulunamadı: " + str(MODEL_DIR)
    if not (_app() / "Contents" / "Info.plist").is_file():
        return "JARVIS 2.app bulunamadı; önce Ayarlar › UYGULAMA › Masaüstü uygulamasını kur."
    return ""


def is_enabled() -> bool:
    return plist_path().is_file()


def install() -> None:
    err = install_problem()
    if err:
        raise RuntimeError(err)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(definition(), sort_keys=False))
    subprocess.run(["/bin/launchctl", "bootout", DOMAIN, str(path)], capture_output=True, check=False)
    result = subprocess.run(["/bin/launchctl", "bootstrap", DOMAIN, str(path)], capture_output=True, text=True)
    if result.returncode:
        path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or "launchctl bootstrap başarısız")


def uninstall() -> None:
    path = plist_path()
    if path.exists():
        subprocess.run(["/bin/launchctl", "bootout", DOMAIN, str(path)], capture_output=True, check=False)
        path.unlink()


def status() -> bool:
    result = subprocess.run(["/bin/launchctl", "print", f"{DOMAIN}/{LABEL}"], capture_output=True, text=True)
    return result.returncode == 0 and "state = running" in result.stdout


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    if action == "run":
        main()
    elif action == "install":
        install()
        print("JARVIS 2 \"Hey Jarvis\" dinleyicisi kuruldu.")
    elif action == "uninstall":
        uninstall()
        print("JARVIS 2 \"Hey Jarvis\" dinleyicisi kaldırıldı.")
    else:
        print("çalışıyor" if status() else "çalışmıyor")
