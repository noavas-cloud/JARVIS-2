"""'Ekranı izle' modu: açıkken ekranın küçültülmüş görüntüsü ~1,5 sn'de bir Gemini Live oturumuna gider.

Kameradaki (WebcamStreamer) gibi yalnızca EN GÜNCEL kare tutulur. Güvenlik/gizlilik:
  * Yalnızca kullanıcı açınca çalışır (sesle "ekranımı izle" ya da F8); 15 dakika sonra kendiliğinden kapanır.
  * JARVIS'in kendi penceresi öndeyken kare alınmaz (kullanıcının işini değil JARVIS'i göstermesin).
  * Kareler diske kalıcı yazılmaz; geçici dosya hemen silinir.
İzin: ekranı JARVIS uygulaması kaydeder → Privacy & Security › Screen & System Audio Recording › JARVIS.
"""

from __future__ import annotations

import ctypes
import io
import os
import subprocess
import tempfile
import threading
import time

from PIL import Image


def _coregraphics():
    return ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")


def screen_permission(request: bool = False) -> bool:
    """Bu süreç (JARVIS) ekran kaydı iznine sahip mi? request=True ise macOS izin penceresini açtırır."""
    try:
        cg = _coregraphics()
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if cg.CGPreflightScreenCaptureAccess():
            return True
        if request:
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            return bool(cg.CGRequestScreenCaptureAccess())
    except (OSError, AttributeError):
        pass
    return False


class ScreenWatcher:
    INTERVAL = 1.5
    MAX_DIM = 1280
    JPEG_QUALITY = 70
    AUTO_OFF_SECONDS = 15 * 60

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._generation = 0
        self._latest: bytes | None = None
        self._started_at = 0.0
        # Kendiliğinden kapanınca (süre doldu / yakalama başarısız) çağrılır: on_ended(reason)
        self.on_ended = None

    @property
    def is_active(self) -> bool:
        return self._active

    def minutes_left(self) -> int:
        if not self._active:
            return 0
        return max(0, int((self.AUTO_OFF_SECONDS - (time.monotonic() - self._started_at)) // 60))

    def get_latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest

    def start(self) -> str:
        """"ok", "already_active", "permission_needed" veya "capture_failed"."""
        with self._lock:
            if self._active:
                self._started_at = time.monotonic()   # yeniden istenince süre baştan başlar
                return "already_active"
        if not screen_permission(request=True):
            return "permission_needed"
        frame = self._capture()
        if frame is None:
            return "capture_failed"
        with self._lock:
            self._active = True
            self._generation += 1
            generation = self._generation
            self._latest = frame
            self._started_at = time.monotonic()
        threading.Thread(target=self._run, args=(generation,), daemon=True).start()
        return "ok"

    def stop(self):
        with self._lock:
            self._active = False
            self._generation += 1
            self._latest = None

    def _current(self, generation: int) -> bool:
        with self._lock:
            return self._active and self._generation == generation

    @staticmethod
    def _jarvis_in_front() -> bool:
        try:
            from actions.screen_vision import _front_app_pid
            return _front_app_pid() == os.getpid()
        except Exception:
            return False

    def _capture(self) -> bytes | None:
        fd, path = tempfile.mkstemp(prefix="jarvis-watch-", suffix=".jpg")
        os.close(fd)
        try:
            result = subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "jpg", "-D", "1", path],
                                    capture_output=True, timeout=10)
            if result.returncode != 0 or os.path.getsize(path) == 0:
                return None
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.thumbnail((self.MAX_DIM, self.MAX_DIM), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=self.JPEG_QUALITY)
                return out.getvalue()
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _run(self, generation: int):
        failures = 0
        while self._current(generation):
            if time.monotonic() - self._started_at > self.AUTO_OFF_SECONDS:
                self.stop()
                if self.on_ended:
                    self.on_ended("timeout")
                return
            if not self._jarvis_in_front():
                frame = self._capture()
                if frame is None:
                    failures += 1
                    if failures >= 5:
                        self.stop()
                        if self.on_ended:
                            self.on_ended("capture_failed")
                        return
                else:
                    failures = 0
                    with self._lock:
                        if self._current_unlocked(generation):
                            self._latest = frame
            time.sleep(self.INTERVAL)

    def _current_unlocked(self, generation: int) -> bool:
        return self._active and self._generation == generation
