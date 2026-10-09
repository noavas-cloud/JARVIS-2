"""Grafik penceresi tarafında el takip sürecini yönetir.

Takip süreci yalnızca: pencere açık + el kontrolü AÇIK + pencere simge durumunda
değilken çalışır. Kapatıldığında süreç sonlandırılır, kamera serbest kalır.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from brain import settings
from brain.hand_setup import EXPLAIN, TRACKER_SCRIPT, env_python


class TrackerClient:
    def __init__(self, python: Path | None = None, script: Path | None = None, extra_args=None):
        self.python = Path(python) if python else env_python()
        self.script = Path(script) if script else TRACKER_SCRIPT
        self.extra_args = list(extra_args or [])
        self.camera_args: list[str] = []     # pencere belirler: --camera auto|N --prefer N
        self.proc: subprocess.Popen | None = None
        self.latest: dict | None = None     # yalnızca en güncel el karesi (eski kareler birikmez)
        self.messages: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.Lock()
        self.started_at = 0.0
        self.paused = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        if not self.python.exists():
            self.messages.put({"type": "error", "code": "missing_env", "message": EXPLAIN["missing_env"]})
            return False
        cmd = [str(self.python), "-u", str(self.script), "--model", str(settings.HAND_MODEL_PATH),
               *self.extra_args, *self.camera_args]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as exc:
            self.messages.put({"type": "error", "code": "internal", "message": f"Başlatılamadı: {exc}"})
            self.proc = None
            return False
        self.started_at = time.monotonic()
        self.paused = False
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        return True

    def _reader(self, proc: subprocess.Popen):
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            # Durdurulmuş eski sürecin iletileri (ör. yeniden başlatmadan sonra gelen "exited")
            # yeni oturumu hata durumuna düşürüp el katmanını gizliyordu.
            if proc is not self.proc:
                continue
            if msg.get("type") == "hands":
                with self._lock:
                    self.latest = msg
            else:
                self.messages.put(msg)
        code = proc.wait()
        try:
            proc.stdout.close()
        except OSError:
            pass
        if proc is self.proc:
            self.messages.put({"type": "exited", "code": code})

    def take_latest(self) -> dict | None:
        with self._lock:
            msg, self.latest = self.latest, None
        return msg

    def _send(self, obj: dict) -> None:
        if self.running and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def pause(self):
        if self.running and not self.paused:
            self.paused = True
            self._send({"cmd": "pause"})

    def resume(self):
        if self.running and self.paused:
            self.paused = False
            self._send({"cmd": "resume"})

    def stop(self, timeout: float = 2.0) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        with self._lock:
            self.latest = None
