"""Durable, sequential tool plans. All outputs come from the existing dispatcher.

``await manager.start(title, steps)`` accepts a list or JSON array of 1–8 steps:
    {"id": "lookup", "label": "Find document", "tool": "find_file", "args": {...}}
Later arguments can use exact references, e.g.
    {"source": {"$ref": "lookup.result.matches.0.path"}}
References are data, never expressions. A failed/ambiguous step stops the plan.
Completed steps are retained; uncertain writes are never automatically repeated.
The host must supply its real dispatcher and preserve its normal permission checks.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import json
from pathlib import Path
import re
import time
import uuid

from actions.local_state import LocalState
from actions.request_checkpoint import read_only


WORKFLOW_TOOLS = frozenset({
    "sys_info", "get_calendar_events", "get_reminders", "get_weather", "search_web",
    "find_file", "smart_search", "research_topic", "get_shared_memory", "get_active_context", "analyze_current",
    "get_activity_history", "get_conversation_history", "get_action_history",
    "prepare_day", "status_report", "assess_situation", "diagnose_slow_mac",
    "diagnose_problem", "get_proactive_advice", "list_watches", "simulate_action",
    "open_app", "move_file", "rename_file", "add_calendar_event", "add_reminder",
    "save_memory",
})
MAX_STEPS = 8
MAX_PLAN_BYTES = 24_000
MAX_OPEN_PLANS = 20
MAX_RESULT_BYTES = 300_000
MAX_STATUS_RESULT_CHARS = 4_000
SUCCESS = frozenset({"ok", "done", "success", "completed"})
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _failed(value, _tool=""):
    value = _payload(value)
    if isinstance(value, dict):
        status = str(value.get("status", "")).lower()
        if status:
            return status not in SUCCESS
        return value.get("ok") is False or value.get("success") is False or bool(value.get("error"))
    return bool(re.match(r"^(hata|error|err)\s*:", str(value or "").strip(), re.I))


def _references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("Sonuç bağlantısı yalnızca {$ref: 'adım.result.alan'} biçiminde olmalı.")
            yield value["$ref"]
        else:
            for item in value.values():
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


class WorkflowManager:
    def __init__(self, execute, allowed_tools=WORKFLOW_TOOLS, is_read_only=read_only,
                 is_error=None, on_update=None, store_path=None, step_timeout=120):
        self.execute = execute
        # A caller can narrow this list, never add shell/GUI/phone/recursive tools.
        self.allowed_tools = frozenset(allowed_tools) & WORKFLOW_TOOLS
        self.is_read_only = is_read_only
        self.is_error = is_error or _failed
        self.on_update = on_update
        self.step_timeout = max(0.01, min(float(step_timeout), 180))
        self.store = LocalState(store_path or Path(__file__).resolve().parents[1] / "memory" / "workflows.json")
        self.active = set()
        self.load_error = ""
        try:
            self.data = self.store.read({"version": 1, "workflows": {}, "latest": None})
            if (not isinstance(self.data, dict) or self.data.get("version") != 1
                    or not isinstance(self.data.get("workflows"), dict)):
                raise ValueError("Geçersiz iş akışı kaydı.")
            for plan in self.data["workflows"].values():
                self._validate(plan["steps"])
                for row in plan["steps"]:
                    if row["status"] == "running":
                        safe = self.is_read_only(row["tool"], row.get("resolved_args", row["args"]))
                        row["status"] = "pending" if safe else "needs_review"
                        row["message"] = ("Kesilen okuma devam ettirilebilir." if safe else
                                          "Kapanma anındaki sonuç belirsiz; bu değişiklik tekrarlanmayacak.")
                self._state(plan)
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            # Do not silently overwrite a corrupt receipt and lose replay safety.
            self.load_error = "İş akışı kaydı okunamadı; kayıt korunuyor: " + str(exc)[:160]
            self.data = {"version": 1, "workflows": {}, "latest": None}

    def _validate(self, raw):
        if isinstance(raw, str):
            if len(raw.encode("utf-8")) > MAX_PLAN_BYTES:
                raise ValueError("İş akışı planı çok uzun.")
            raw = json.loads(raw)
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
            raise ValueError("İş akışı 1–8 sıralı adımdan oluşmalı.")
        specs, seen = [], set()
        for index, spec in enumerate(raw, 1):
            if not isinstance(spec, dict):
                raise ValueError("Her adım bir nesne olmalı.")
            key = spec.get("id", f"step{index}")
            if not isinstance(key, str) or not _ID.fullmatch(key) or key in seen:
                raise ValueError("Adımların kimlikleri geçerli ve birbirinden farklı olmalı.")
            tool = spec.get("tool", "")
            if tool not in self.allowed_tools:
                raise ValueError(f"Bu araç sıralı iş akışına açık değil: {tool}")
            args = spec.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("Adımın args alanı bir nesne olmalı.")
            if len(_json(args).encode("utf-8")) > MAX_PLAN_BYTES:
                raise ValueError("Adım parametreleri çok uzun.")
            for reference in _references(args):
                parts = reference.split(".")
                if len(parts) < 2 or parts[0] not in seen or parts[1] != "result" or any(not p for p in parts):
                    raise ValueError("Sonuç bağlantısı yalnızca daha önceki bir adımın result alanını kullanabilir.")
            seen.add(key)
            specs.append({"id": key, "label": str(spec.get("label") or tool)[:80],
                          "tool": tool, "args": deepcopy(args)})
        if len(_json(specs).encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError("İş akışı planı çok uzun.")
        return specs

    @staticmethod
    def _state(plan):
        states = [row["status"] for row in plan["steps"]]
        plan["status"] = ("cancelled" if plan.get("cancelled") and "running" not in states else
                          "done" if all(s == "done" for s in states) else
                          "needs_review" if "needs_review" in states else
                          "error" if "error" in states else
                          "needs_input" if "needs_input" in states else
                          "running" if "running" in states else "pending")

    def _save(self, plan):
        self._state(plan)
        plan["updated"] = time.time()
        self.store.write(self.data)
        if self.on_update:
            rows = [{"task_id": f"{plan['id']}-{r['id']}", "label": r["label"],
                     "status": "cancelled" if plan.get("cancelled") and r["status"] == "pending" else r["status"],
                     "progress_percent": 100 if r["status"] == "done" else None,
                     "result": r.get("message", "")} for r in plan["steps"]]
            try:
                self.on_update(rows)
            except Exception:
                pass  # Display failure cannot invalidate a durable operation receipt.

    def _resolve(self, value, previous, mutating):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                key, _, *parts = reference.split(".")
                source = previous[key]
                result = source["result"]
                if source["status"] != "done" or source.get("result_unavailable"):
                    raise ValueError("Önceki adımın doğrulanmış, tam sonucu kullanılamıyor.")
                if mutating and source["tool"] in {"find_file", "smart_search"}:
                    if not isinstance(result, dict):
                        raise ValueError("Dosya hedefi doğrulanamadı.")
                    matches = result.get("matches", [])
                    if (len(matches) != 1 or result.get("found", 1) != 1
                            or result.get("truncated") or result.get("inaccessible")):
                        raise ValueError("Dosya araması tek ve kesin bir hedef vermedi; önce kullanıcıyla hedefi netleştir.")
                try:
                    for part in parts:
                        if isinstance(result, dict):
                            result = result[part]
                        elif isinstance(result, list) and part.isdigit():
                            result = result[int(part)]
                        else:
                            raise KeyError(part)
                except (KeyError, IndexError, TypeError) as exc:
                    raise ValueError(f"Gerçek sonuçta bu alan yok: {reference}") from exc
                return deepcopy(result)
            return {k: self._resolve(v, previous, mutating) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v, previous, mutating) for v in value]
        return deepcopy(value)

    def _get(self, key):
        return self.data["workflows"].get(key or self.data.get("latest"))

    async def start(self, title, steps):
        if self.load_error:
            return _json({"status": "error", "message": self.load_error})
        try:
            specs = self._validate(steps)
        except (ValueError, TypeError, RecursionError) as exc:
            return _json({"status": "error", "message": str(exc)})
        plans = self.data["workflows"]
        if sum(p["status"] not in {"done", "cancelled"} for p in plans.values()) >= MAX_OPEN_PLANS:
            return _json({"status": "error", "message": "20 yarım iş akışı kayıtlı; önce mevcut işleri incele."})
        key = uuid.uuid4().hex[:12]
        plan = {"id": key, "title": str(title or "Sıralı görev")[:160], "created": time.time(),
                "steps": [dict(spec, status="pending", result=None) for spec in specs]}
        plans[key] = plan
        self.data["latest"] = key
        finished = [k for k, v in plans.items() if v.get("status") in {"done", "cancelled"}]
        for old in finished[:-20]:
            del plans[old]
        self._save(plan)
        return await self._run(plan)

    async def resume(self, workflow_id=""):
        if self.load_error:
            return _json({"status": "error", "message": self.load_error})
        plan = self._get(workflow_id)
        if not plan:
            return _json({"status": "error", "message": "İş akışı bulunamadı."})
        if plan["id"] in self.active or plan["status"] in {"done", "cancelled", "needs_review", "needs_input"}:
            return self.status(plan["id"])
        for row in plan["steps"]:
            if row["status"] == "error" and self.is_read_only(row["tool"], row.get("resolved_args", row["args"])):
                row["status"] = "pending"
        return await self._run(plan)

    def cancel(self, workflow_id=""):
        """Stop future steps, retaining receipts; never undo or interrupt a write."""
        if self.load_error:
            return _json({"status": "error", "message": self.load_error})
        plan = self._get(workflow_id)
        if not plan:
            return _json({"status": "error", "message": "İş akışı bulunamadı."})
        if plan["status"] != "done":
            plan["cancelled"] = True
            self._save(plan)
        result = json.loads(self.status(plan["id"]))
        # The cancellation request itself is durably accepted even if the current
        # tool is still returning. This is not an uncertain external mutation.
        result["status"] = "ok"
        return _json(result)

    async def _run(self, plan):
        key = plan["id"]
        if key in self.active:
            return self.status(key)
        self._validate(plan["steps"])  # Recheck restored plans against today's allowlist.
        self.active.add(key)
        try:
            previous = {}
            for row in plan["steps"]:
                if plan.get("cancelled"):
                    break
                if row["status"] == "done":
                    previous[row["id"]] = row
                    continue
                if row["status"] != "pending":
                    break
                safe = self.is_read_only(row["tool"], row["args"])
                try:
                    args = self._resolve(row["args"], previous, not safe)
                    # References may change parameters relevant to read-only classification.
                    safe = self.is_read_only(row["tool"], args)
                except (ValueError, KeyError, TypeError) as exc:
                    row.update(status="needs_input", message=str(exc))
                    self._save(plan)
                    break
                row.update(status="running", resolved_args=args, message="Çalışıyor.")
                self._save(plan)  # Intent must reach disk before the dispatcher is invoked.
                try:
                    call = self.execute(row["tool"], deepcopy(args))
                    if not inspect.isawaitable(call):
                        raise TypeError("İş akışı yürütücüsü async olmalı.")
                    raw = await asyncio.wait_for(call, timeout=self.step_timeout)
                    result = _payload(raw)
                    serialized = _json(result)
                    # Preserve receipt without unbounded disk growth. Such a result cannot
                    # serve as input to a later mutation; its full data must be queried again.
                    row["result_unavailable"] = len(serialized.encode("utf-8")) > MAX_RESULT_BYTES
                    row["result"] = serialized[:MAX_STATUS_RESULT_CHARS] if row["result_unavailable"] else result
                    # Salt okunur adımlarda "partial" (ör. pilsiz Mac'te status_report, prepare_day'de bir
                    # kaynağın okunamaması) kısmi ama kullanılabilir sonuçtur; iş akışını durdurmaz.
                    # Değişiklik yapan adımlarda partial hâlâ durdurur (sonuç doğrulanmalı).
                    ok_states = SUCCESS | {""} | ({"partial"} if safe else set())
                    explicit_stop = isinstance(result, dict) and (
                        str(result.get("status", "")).lower() not in ok_states
                        or result.get("ok") is False or result.get("success") is False)
                    missing = result is None or (isinstance(result, str) and not result.strip())
                    failed = missing or explicit_stop or self.is_error(raw, row["tool"])
                    row["status"] = ("error" if safe else "needs_review") if failed else "done"
                    row["message"] = ("Adım tamamlanamadı; sonraki adımlar çalıştırılmadı." if safe else
                                      "Değişiklik doğrulanamadı; tekrar edilmedi, gerçek sonuç kontrol edilmeli.") if failed else "Tamamlandı."
                except asyncio.CancelledError:
                    row.update(status="pending" if safe else "needs_review",
                               message="İş kesildi. Tamamlanan adımlar korunuyor; sonucu belirsiz değişiklikler tekrarlanmaz.")
                    self._save(plan)
                    raise
                except Exception as exc:
                    row.update(status="error" if safe else "needs_review", result=None,
                               message=f"{type(exc).__name__}: {str(exc)[:300] or 'Adımın sonucu alınamadı.'}")
                self._save(plan)
                if row["status"] != "done":
                    break
                previous[row["id"]] = row
        finally:
            self.active.discard(key)
        return self.status(key)

    def status(self, workflow_id=""):
        if self.load_error:
            return _json({"status": "error", "message": self.load_error})
        plan = self._get(workflow_id)
        if not plan:
            return _json({"status": "ok", "message": "Kayıtlı iş akışı yok.", "workflows": []})
        rows = []
        for row in plan["steps"]:
            result = row.get("result")
            encoded = _json(result)
            if len(encoded) > MAX_STATUS_RESULT_CHARS:
                result = {"preview": encoded[:MAX_STATUS_RESULT_CHARS], "truncated": True}
            rows.append({"id": row["id"], "label": row["label"], "tool": row["tool"],
                         "status": row["status"], "result": result, "message": row.get("message", "")})
        done = sum(r["status"] == "done" for r in rows)
        summary = f"{plan['title']}: {done}/{len(rows)} adım tamamlandı."
        blocked = next((r for r in rows if r["status"] in {"error", "needs_review", "needs_input"}), None)
        if blocked:
            summary += f" {blocked['label']}: {blocked['message']}"
        if plan.get("cancelled"):
            summary += " İş akışı durduruldu; yeni adım başlatılmayacak. Tamamlanan işlemler geri alınmadı."
            if plan["id"] in self.active:
                summary += " Çalışan adımın sonucu bekleniyor."
        return _json({"status": "ok" if plan["status"] in {"done", "cancelled"} else "partial",
                      "workflow_id": plan["id"], "title": plan["title"], "workflow_status": plan["status"],
                      "steps": rows, "spoken_summary": summary,
                      "workflows": [{"workflow_id": p["id"], "title": p["title"], "status": p["status"]}
                                    for p in list(self.data["workflows"].values())[-20:]]})
