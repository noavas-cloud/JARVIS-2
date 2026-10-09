"""İkinci Beyin bağlantısı. Grafik penceresi ve el kontrolü asıl JARVIS'tekiyle aynı koddur (brain/, dokunulmadı);
burada yalnız ayrı süreçteki pencerenin başlatılması, olayları ve sesli araç (second_brain) yönetilir."""

from __future__ import annotations

import asyncio
import json
import threading


class BrainHost:
    def __init__(self, state):
        self.state = state
        self._launcher = None
        self._lock = threading.Lock()

    # ── Pencere ─────────────────────────────────────────────────────────────────────────────
    def launcher(self):
        with self._lock:
            if self._launcher is None:
                from brain.launcher import BrainLauncher
                self._launcher = BrainLauncher(on_event=self._on_event)
            return self._launcher

    @property
    def running(self) -> bool:
        return bool(self._launcher is not None and self._launcher.running)

    @property
    def selection(self):
        return getattr(self._launcher, "selection", None) if self._launcher else None

    def open(self, focus: str = "") -> str:
        try:
            state = self.launcher().open(focus)
        except Exception as exc:
            self.state.log("err", f"İkinci Beyin açılamadı: {exc}")
            return "error"
        if state == "opened":
            self.state.log("sys", "İkinci Beyin ayrı pencerede açılıyor…")
        return state

    def start_index(self) -> str:
        try:
            return self.launcher().start_index()
        except Exception as exc:
            print(f"[BEYİN] Tarama başlatılamadı: {exc}", flush=True)
            return "error"

    def set_hand_control(self, enabled: bool):
        if self.running:
            self._launcher.set_hand_control(enabled)

    def shutdown(self):
        if self._launcher is not None:
            try:
                self._launcher.shutdown()
            except Exception:
                pass

    def _on_event(self, event: dict):
        kind = event.get("event")
        if kind == "hand_status":
            print("[BEYİN] El kontrolü: " + str(event.get("message", "")), flush=True)
        elif kind in ("indexed", "index_done"):
            stats = event.get("stats") or event
            self.state.log("sys", f"İkinci Beyin güncellendi: {stats.get('files', 0)} dosya, "
                                  f"{stats.get('edges', 0)} bağlantı.")
            if kind == "index_done" and self._launcher is not None:
                self._launcher.reload()
        elif kind == "index_error":
            self.state.log("sys", "İkinci Beyin taraması tamamlanamadı: " + str(event.get("message", ""))[:160])
        self.state.post("brain", kind)

    # ── Sesli araç ──────────────────────────────────────────────────────────────────────────
    async def tool(self, args: dict) -> str:
        """İkinci beyin: yalnızca kullanıcının seçtiği klasörlerin yerel indeksinden yanıt verir."""
        from brain import voice_control
        from brain.query import second_brain
        action = str(args.get("action", "query") or "query").strip().lower()
        query_text = str(args.get("query", "") or "").strip()
        try:
            limit = int(args.get("limit", 12) or 12)
        except (TypeError, ValueError):
            limit = 12
        if action in voice_control.CONTROL_ACTIONS:
            # Sesle grafik yönetimi: plan indeksten yapılır, komutlar ayrı süreçteki pencereye gider.
            args = dict(args, _selection=self.selection)
            plan = await asyncio.to_thread(voice_control.plan, action, args)
            result = plan["result"]
            launcher = self.launcher()
            window = "running" if launcher.running else "closed"
            if not launcher.running and plan["open_window"] and result.get("status") in ("ok", "not_indexed"):
                launcher.open("")
                window = "opened"
            sent = 0
            if launcher.running:
                for cmd in plan["commands"]:
                    sent += bool(launcher.send(cmd))
            if action == "hand_control":
                self.state.post("brain", "hand_control")
            result["window"] = window
            result["commands_sent"] = sent
            return json.dumps(result, ensure_ascii=False)
        if action == "reindex":
            state = self.start_index()
            messages = {"started": "Seçili klasörler arka planda yeniden taranıyor.",
                        "window": "Tarama İkinci Beyin penceresinde başladı.",
                        "busy": "Zaten bir tarama sürüyor.",
                        "needs_setup": "Kaynak klasör seçilmemiş. Ayarlar › İKİNCİ BEYİN › Kaynak klasörler."}
            return json.dumps({"status": "needs_setup" if state == "needs_setup" else "ok", "state": state,
                               "message": messages.get(state, "Tarama başlatılamadı.")}, ensure_ascii=False)
        data = json.loads(await asyncio.to_thread(second_brain, action, query_text, limit))
        status = data.get("status")
        if status == "not_indexed" and not data.get("indexing"):
            data["indexing_started"] = self.start_index()
        if action == "read" and data.get("excerpts") and self.running:
            self._launcher.send({"cmd": "control", "op": "select", "id": data["excerpts"][0]["id"]})
        if action == "show":
            if status == "needs_setup":
                self.state.post("open_brain_sources")
                data["window"] = "sources_dialog_opened"
            else:
                focus = (data.get("focus") or {}).get("title") or ""
                self.open(focus or query_text)
                data["window"] = "opened"
        return json.dumps(data, ensure_ascii=False)
