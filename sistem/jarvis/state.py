"""Çekirdek ↔ arayüz arasındaki paylaşılan durum.

Eski JARVIS'te çekirdek arayüz nesnesinin alanlarını doğrudan değiştiriyor, arayüz de iş parçacıklarından
root.after çağrıları alıyordu. Burada çekirdek yalnız bu nesneye yazar ve olay kuyruğuna ileti bırakır;
arayüz her karede (≈30/sn) durumu okur ve kuyruğu boşaltır. Tk yalnız ana iş parçacığında çalışır.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ToolEvent:
    name: str
    label: str
    started: float
    finished: float = 0.0
    ok: bool | None = None          # None = sürüyor


@dataclass
class State:
    status: str = "INITIALISING"    # INITIALISING · LISTENING · THINKING · SPEAKING · ERROR
    muted: bool = False
    paused: bool = False
    cloud: bool = False             # Gemini bağlı mı
    speaking: bool = False
    tool: str = ""                  # çalışan aracın adı ("" = yok)
    mic_level: float = 0.0          # 0..1
    mic_mode: str = "BAĞLANIYOR"
    mic_device: str = ""
    output_level: float = 0.0       # 0..1 (JARVIS'in sesi)
    heard: str = ""                 # son duyulan cümle
    user_speaking_until: float = 0.0
    webcam: bool = False
    screen_watch: bool = False
    browsing: bool = False          # Chrome araştırma ajanı çalışıyor (kürede "CHROME'DA GEZİNİYOR")
    browser_visible: bool = False   # JARVIS Chrome penceresi şu an görünür
    error_at: float = -1.0e9
    success_at: float = -1.0e9
    error_hold_until: float = 0.0
    tool_events: list = field(default_factory=list)
    task_rows: list = field(default_factory=list)      # paralel görev / görev zinciri satırları (son grup)
    task_rows_at: float = 0.0
    events: "queue.Queue" = field(default_factory=queue.Queue)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Çekirdekten çağrılır (her iş parçacığından güvenli) ─────────────────────────────────
    def post(self, kind: str, *payload) -> None:
        self.events.put((kind,) + payload)

    def log(self, who: str, text: str) -> None:
        """who: you · ai · sys · err · note (bildirim)"""
        text = str(text or "").strip()
        if not text:
            return
        if who == "you":
            self.mark_user_activity()
            self.set_status("THINKING")
        elif who == "err":
            self.error_hold_until = time.time() + 6.0
            self.set_status("ERROR")
        self.post("log", who, text)

    def set_status(self, status: str) -> None:
        previous = self.status
        if status == "ERROR" and previous != "ERROR":
            self.error_at = time.monotonic()
        self.status = status
        self.speaking = status == "SPEAKING"
        if status != previous:
            self.post("status", previous, status)

    def mark_user_activity(self) -> None:
        self.user_speaking_until = time.time() + 0.9

    def tool_started(self, name: str, label: str) -> None:
        with self.lock:
            self.tool = name
            self.tool_events.append(ToolEvent(name, label, time.time()))
            del self.tool_events[:-12]

    def tool_finished(self, name: str, ok: bool) -> None:
        with self.lock:
            self.tool = ""
            for ev in reversed(self.tool_events):
                if ev.name == name and ev.ok is None:
                    ev.ok, ev.finished = ok, time.time()
                    break
        if ok:
            self.success_at = time.monotonic()

    def set_tasks(self, rows) -> None:
        """Paralel görev / görev zinciri ilerlemesi (arka plandaki işten gelir)."""
        rows = [dict(r) for r in rows or []][:6]
        with self.lock:
            if rows != self.task_rows:          # aynı satırların yeniden bildirilmesi "yeni" sayılmaz
                self.task_rows = rows
                self.task_rows_at = time.time()

    def visible_tasks(self, keep: float = 600.0) -> list:
        """Süren bir iş varsa ya da son değişiklik 10 dk içindeyse satırlar; yoksa boş."""
        with self.lock:
            rows = list(self.task_rows)
            fresh = time.time() - self.task_rows_at < keep
        live = any(r.get("status") in ("pending", "running", "waiting_network") for r in rows)
        return rows if rows and (live or fresh) else []

    def recent_tools(self) -> list:
        with self.lock:
            return list(self.tool_events)

    # ── Arayüzden çağrılır ─────────────────────────────────────────────────────────────────
    def drain(self, limit: int = 50) -> list:
        out = []
        for _ in range(limit):
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out
