"""Independent desktop tasks with durable steps and resumable HTTPS downloads."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
import uuid
from actions.local_state import LocalState
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

from actions.calendar import get_calendar_events
from actions.open_app import open_app
from actions.reminders import get_reminders
from actions.sys_info import sys_info
from actions.weather import get_weather_summary
from actions.web_search import search_web


DOWNLOADS = Path.home() / "Downloads"
MAX_DOWNLOAD_BYTES = 2 * 1024**3
CHUNK_BYTES = 128 * 1024
KINDS = {"download", "calendar", "reminders", "weather", "open_app", "web_search", "sys_info"}
LABELS = {"download": "İndirme", "calendar": "Takvim", "reminders": "Anımsatıcılar",
          "weather": "Hava", "open_app": "Uygulama", "web_search": "İnternet araması", "sys_info": "Sistem"}


def _check_url(raw: str) -> str:
    url = str(raw or "").strip()
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("İndirme için doğrudan HTTPS dosya bağlantısı gerekli.")
    if host.lower() == "localhost" or host.lower().endswith((".local", ".localhost")):
        raise ValueError("Yerel ağ adresinden indirme desteklenmiyor.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Doğrudan IP adresinden indirme desteklenmiyor.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Geçersiz bağlantı noktası.") from exc
    if port not in (None, 443):
        raise ValueError("Yalnızca standart HTTPS bağlantısı destekleniyor.")
    # Reject private DNS answers before requests opens a connection.
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError("Bağlantı herkese açık bir sunucuya yönlenmiyor.")
    return url


def _safe_name(value: str) -> str:
    decoded = unquote(str(value or ""))
    name = decoded.replace("\\", "/").split("/")[-1].strip().strip(".")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    if not name or name in {".", ".."}:
        raise ValueError("Dosya adı belirlenemedi; doğrudan dosya bağlantısı söyle.")
    return name[:180]


def _available_path(folder: Path, filename: str) -> Path:
    original = folder / filename
    if not os.path.lexists(original):
        return original
    stem, suffix = original.stem, original.suffix
    for number in range(2, 1001):
        candidate = folder / f"{stem} ({number}){suffix}"
        if not os.path.lexists(candidate):
            return candidate
    raise OSError("Bu dosya için boş ad bulunamadı.")


def download_file(url: str, progress, folder: Path = DOWNLOADS, checkpoint_path=None) -> dict:
    from actions.resumable_download import download_file as transfer
    return transfer(url, progress, folder, checkpoint_path)


def validate_task(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Her görev bir nesne olmalı.")
    kind = str(spec.get("kind", "")).strip()
    if kind not in KINDS:
        raise ValueError("Desteklenmeyen görev türü.")
    target = str(spec.get("target", "") or "").strip()
    query = str(spec.get("query", "") or "").strip()
    day = str(spec.get("day", "") or "").strip()
    location = str(spec.get("location", "") or "").strip()
    if kind == "download":
        # Syntax validation here; DNS validation occurs in the worker.
        parsed = urlsplit(target)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("İndirme için kesin HTTPS dosya bağlantısı gerekli.")
    elif kind == "open_app" and not target:
        raise ValueError("Açılacak uygulamanın adı gerekli.")
    elif kind in ("web_search", "sys_info") and not query:
        raise ValueError("Arama veya sistem sorgusu gerekli.")
    elif kind == "sys_info" and query not in ("battery", "cpu", "ram", "disk", "time", "date", "network", "all"):
        raise ValueError("Geçersiz sistem sorgusu.")
    elif kind == "weather" and day and day not in ("now", "today", "tomorrow"):
        raise ValueError("Hava günü now, today veya tomorrow olmalı.")
    label = LABELS[kind]
    if kind == "open_app":
        label = target[:40]
    elif kind == "download":
        label = "İndirme: " + (urlsplit(target).path.rstrip("/").split("/")[-1] or "dosya")[:40]
    return {"kind": kind, "target": target, "query": query, "day": day,
            "location": location, "label": label}


def _discard_transfer(row: dict) -> None:
    path = (row.get("spec") or {}).get("_transfer_path")
    if path:
        from actions.resumable_download import discard_transfer
        try:
            discard_transfer(path)
        except Exception:
            pass


def run_task(spec: dict, progress=lambda _received, _total: None) -> dict:
    kind = spec["kind"]
    if kind == "download":
        return download_file(spec["target"], progress, checkpoint_path=spec.get("_transfer_path"))
    if kind == "calendar":
        result = get_calendar_events(spec["query"] or "tomorrow")
    elif kind == "reminders":
        result = get_reminders(spec["query"] or "upcoming")
    elif kind == "weather":
        result = get_weather_summary(spec["location"] or None, spec["day"] or "now")
    elif kind == "open_app":
        result = open_app(spec["target"])
    elif kind == "web_search":
        result = search_web(spec["query"])
    else:
        result = sys_info(spec["query"])
    text = str(result or "")
    lowered = text.strip().casefold()
    failed = lowered.startswith(("hata:", "error:", "takvim okunamadi", "takvim okunamadı",
                                  "takvim erisim izni", "animsatici erisim izni",
                                  "anımsatıcılar okunamadı", "animsaticilar okunamadi"))
    if kind == "open_app" and any(part in lowered for part in ("bulunamadı", "açılamadı", "zaman aşımı")):
        failed = True
    if failed and kind in ("weather", "web_search") and any(word in lowered for word in ("ulaşılamadı", "zaman aşımına")):
        return {"status": "waiting_network", "result": "Servis bağlantısı bekleniyor; tamamlanan adımlar korunuyor."}
    return {"status": "error" if failed else "done", "result": text}


class ParallelTaskManager:
    """Persist every step before and after execution; never rerun completed rows."""

    def __init__(self, on_update, store_path=None):
        self.on_update = on_update
        self.store = LocalState(store_path or Path(__file__).resolve().parents[1] / "memory" / "parallel_tasks.json")
        data = self.store.read({"batches": {}, "latest_batch": None})
        self.batches = data["batches"]
        self.latest_batch = data["latest_batch"]
        self.jobs = set()
        self.active = set()
        for rows in self.batches.values():
            for row in rows:
                if row["status"] == "running":
                    row["status"] = "needs_review" if row["kind"] == "open_app" else "pending"
                    if row["status"] == "needs_review":
                        row["result"] = "Kapanma anındaki sonuç belirsiz; uygulama yeniden açılmadı."
        self._save()

    def _save(self):
        self.store.write({"version": 1, "batches": self.batches, "latest_batch": self.latest_batch})

    def _publish(self, batch_id):
        self._save()
        if batch_id == self.latest_batch:
            self.on_update([{k: v for k, v in row.items() if k != "spec"} for row in self.batches[batch_id]])

    def _progress(self, batch_id, task_id, received, total):
        for row in self.batches.get(batch_id, []):
            if row["task_id"] == task_id and row["status"] == "running":
                row["bytes_received"] = received
                row["progress_percent"] = min(100, round(received * 100 / total)) if total else None
                self._publish(batch_id)
                return

    async def _run(self, batch_id, row):
        row["status"] = "running"
        self._publish(batch_id)
        loop = asyncio.get_running_loop()
        def progress(received, total):
            loop.call_soon_threadsafe(self._progress, batch_id, row["task_id"], received, total)
        worker = asyncio.create_task(asyncio.to_thread(run_task, row["spec"], progress))
        try:
            row.update(await asyncio.shield(worker))
        except asyncio.CancelledError:
            # A network-session cancellation must not lose a completed local step.
            try:
                row.update(await worker)
            except Exception as exc:
                row.update(status="waiting_network" if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError, socket.gaierror)) else "error", result=str(exc))
            self._publish(batch_id)
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError, socket.gaierror) as exc:
            retries = int(row.get("network_retries", 0)) + 1
            if retries >= 20:
                # Yanlış yazılmış alan adı / SSL hatası da bağlantı hatası sayılıyordu: 30 sn'de bir
                # sonsuza dek yeniden denenip toplu iş hiç bitmiyordu.
                row.update(status="error", network_retries=retries,
                           result=f"Bağlantı {retries} denemede kurulamadı ({type(exc).__name__}); adres yanlış olabilir.")
                _discard_transfer(row)
            else:
                row.update(status="waiting_network", network_retries=retries,
                           result="Bağlantı bekleniyor; tamamlanan adımlar korunuyor.")
        except Exception as exc:
            row.update(status="error", result=f"{type(exc).__name__}: {exc}")
        finally:
            self.active.discard(row["task_id"])
        self._publish(batch_id)
        if row.get("status") == "done":
            # Sonuç kalıcı kaydedildi; indirme kaydına artık gerek yok.
            _discard_transfer(row)

    def _launch(self, batch_id, row):
        if row["task_id"] in self.active:
            return None
        self.active.add(row["task_id"])
        job = asyncio.create_task(self._run(batch_id, row))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)
        return job

    async def resume(self, batch_id=""):
        started = 0
        for key, rows in self.batches.items():
            if batch_id and key != batch_id:
                continue
            for row in rows:
                if row["status"] in ("pending", "waiting_network") and row["task_id"] not in self.active:
                    self._launch(key, row)
                    started += 1
        if self.latest_batch:
            self._publish(self.latest_batch)
        await asyncio.sleep(0)
        return json.dumps({"status": "ok", "message": f"{started} yarım kalan adım sürdürüldü. Tamamlanmış adımlar tekrarlanmadı.",
                           "pending_review": sum(r["status"] == "needs_review" for rows in self.batches.values() for r in rows)}, ensure_ascii=False)

    async def watch_resume(self):
        while True:
            await self.resume()
            await asyncio.sleep(30)

    async def start(self, raw_tasks):
        if not isinstance(raw_tasks, list) or not 2 <= len(raw_tasks) <= 4:
            return json.dumps({"status": "error", "message": "Aynı anda 2-4 bağımsız görev verilmeli."}, ensure_ascii=False)
        try:
            specs = [validate_task(item) for item in raw_tasks]
        except (ValueError, TypeError) as exc:
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        batch_id = uuid.uuid4().hex[:12]
        rows = []
        for index, spec in enumerate(specs, 1):
            task_id = f"{batch_id}-{index}"
            if spec["kind"] == "download":
                spec["_transfer_path"] = str(self.store.path.parent / "transfers" / (task_id + ".json"))
            rows.append({"task_id": task_id, "kind": spec["kind"], "label": spec["label"],
                         "status": "pending", "progress_percent": None, "result": "", "spec": spec})
        self.batches[batch_id] = rows
        self.latest_batch = batch_id
        finished = [key for key, value in self.batches.items() if all(r["status"] == "done" for r in value)]
        for key in finished[:-5]:
            del self.batches[key]
        self._publish(batch_id)
        quick = []
        for row in rows:
            job = self._launch(batch_id, row)
            if row["kind"] != "download":
                quick.append(job)
        if quick:
            await asyncio.wait(quick, timeout=24)
        else:
            await asyncio.sleep(0)
        return self.status(batch_id)

    def status(self, batch_id=""):
        batch_id = batch_id or self.latest_batch or ""
        rows = self.batches.get(batch_id)
        if rows is None:
            return json.dumps({"status": "ok", "message": "Kayıtlı görev grubu yok."}, ensure_ascii=False)
        summary = []
        for row in rows:
            state = row["status"]
            if state == "done":
                summary.append(f"{row['label']}: {row['result']}")
            elif state in ("error", "needs_review", "waiting_network"):
                summary.append(f"{row['label']}: {row['result']}")
            elif row.get("progress_percent") is not None:
                summary.append(f"{row['label']} sürüyor: %{row['progress_percent']}.")
            else:
                summary.append(f"{row['label']} sürüyor.")
        return json.dumps({"status": "partial" if any(r["status"] in ("error", "needs_review", "waiting_network") for r in rows) else "ok",
                           "batch_id": batch_id, "tasks": [{k: v for k, v in r.items() if k != "spec"} for r in rows],
                           "spoken_summary": " ".join(summary)}, ensure_ascii=False)
