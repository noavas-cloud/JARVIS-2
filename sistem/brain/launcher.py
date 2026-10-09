"""Ana JARVIS sürecinden İkinci Beyin penceresini ve indeksleyiciyi başlatır.

Ana arayüzün 25 FPS döngüsüne iş yüklemez: pencere ve indeksleyici ayrı
süreçlerdir; burada yalnızca başlatma ve JSON satırı okuma iş parçacıkları vardır.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from brain import BASE_DIR, settings


def _log_handle():
    path = settings.LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:
            path.write_text("", encoding="utf-8")
        return open(path, "a", encoding="utf-8")
    except OSError:
        return subprocess.DEVNULL


class BrainLauncher:
    def __init__(self, on_event=None, python: str | None = None):
        self.on_event = on_event or (lambda ev: None)
        self.python = python or sys.executable
        self.proc: subprocess.Popen | None = None
        self.index_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._selection: dict | None = None   # grafikte son seçilen/odaklanılan düğüm (pencere bildirir)

    @property
    def selection(self) -> dict | None:
        """{'id','label','type','path','focus','seconds_ago'} ya da pencere kapalı/seçim yoksa None."""
        sel = self._selection
        if not sel or not sel.get("id") or not self.running:
            return None
        return {**{k: sel.get(k) for k in ("id", "label", "type", "path", "focus")},
                "seconds_ago": round(time.monotonic() - sel["at"])}

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def indexing(self) -> bool:
        return self.index_proc is not None and self.index_proc.poll() is None

    def _reader(self, proc: subprocess.Popen, kind: str):
        assert proc.stdout is not None
        last = None
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            last = event
            if kind == "window":
                if event.get("event") == "selection":
                    self._selection = {**event, "at": time.monotonic()}
                self.on_event(event)
            elif event.get("type") in ("done", "error", "busy"):
                self.on_event({"event": "index_" + event["type"], **event})
        code = proc.wait()
        if kind == "window":
            self._selection = None
            self.on_event({"event": "closed", "code": code})
        elif last is None or last.get("type") not in ("done", "error", "busy"):
            self.on_event({"event": "index_error", "message": f"Tarama süreci beklenmedik biçimde bitti ({code})."})

    def open(self, focus: str = "") -> str:
        with self._lock:
            if self.running:
                if focus:
                    self.send({"cmd": "focus", "query": focus})
                self.send({"cmd": "raise"})
                return "raised"
            # brain.dockless, brain.window'u Dock'ta ayrı "Python" simgesi göstermeden açar.
            cmd = [self.python, "-m", "brain.dockless", "--ipc"]
            if focus:
                cmd += ["--focus", focus]
            self.proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=_log_handle(), text=True, bufsize=1)
            threading.Thread(target=self._reader, args=(self.proc, "window"), daemon=True).start()
            return "opened"

    def send(self, obj: dict) -> bool:
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def start_index(self) -> str:
        """Seçili klasörleri yeniden tarar. Pencere açıksa taramayı pencere yapar (ilerlemeyi gösterir)."""
        if not settings.get_sources():
            return "needs_setup"
        if self.running and self.send({"cmd": "reindex"}):
            return "window"
        with self._lock:
            if self.indexing:
                return "busy"
            self.index_proc = subprocess.Popen([self.python, "-m", "brain.indexer", "--build"], cwd=str(BASE_DIR),
                                               stdout=subprocess.PIPE, stderr=_log_handle(), text=True, bufsize=1)
            threading.Thread(target=self._reader, args=(self.index_proc, "index"), daemon=True).start()
            return "started"

    def set_hand_control(self, enabled: bool):
        self.send({"cmd": "hand", "enabled": bool(enabled)})

    def reload(self):
        self.send({"cmd": "reload"})

    def shutdown(self):
        """JARVIS kapanırken: pencereye kapan de (kamera varsa bırakılır)."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self.send({"cmd": "quit"})
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=1.5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            try:
                proc.terminate()
            except OSError:
                pass
