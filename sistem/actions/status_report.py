"""One read-only, source-backed status report for the local Mac."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
import os
from pathlib import Path
import socket

import psutil
from app_config import get_app_config_value

from actions.calendar import _parse_payload as parse_events, _run_helper
from actions.reminders import _parse_payload as parse_reminders
from actions.weather import get_weather_summary
from actions.activity_history import DATABASE, get_activity_history


def _unavailable(detail: str) -> dict:
    return {"status": "unavailable", "detail": detail}


def _system() -> dict:
    sections = {}
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            sections["battery"] = _unavailable("Pil bilgisi alınamadı.")
        else:
            minutes = None
            if not battery.power_plugged and battery.secsleft is not None and battery.secsleft > 0:
                minutes = math.ceil(battery.secsleft / 60)
            sections["battery"] = {
                "status": "ok", "percent": round(battery.percent),
                "plugged": bool(battery.power_plugged), "estimated_minutes_left": minutes,
            }
    except Exception:
        sections["battery"] = _unavailable("Pil bilgisi alınamadı.")

    try:
        percent = round(psutil.cpu_percent(interval=0.25), 1)
        sections["cpu"] = {"status": "ok", "percent": percent,
                           "level": "normal" if percent < 70 else "high" if percent < 90 else "very_high"}
    except Exception:
        sections["cpu"] = _unavailable("CPU kullanımı alınamadı.")

    try:
        memory = psutil.virtual_memory()
        sections["memory"] = {"status": "ok", "percent": round(memory.percent),
                              "used_gib": round(memory.used / 1024**3, 1),
                              "total_gib": round(memory.total / 1024**3, 1)}
    except Exception:
        sections["memory"] = _unavailable("Bellek bilgisi alınamadı.")

    try:
        disk = psutil.disk_usage("/")
        sections["disk"] = {"status": "ok", "free_gib": round(disk.free / 1024**3, 1),
                            "total_gib": round(disk.total / 1024**3, 1),
                            "used_percent": round(disk.percent)}
    except Exception:
        sections["disk"] = _unavailable("Depolama bilgisi alınamadı.")
    return sections


def _calendar(now: datetime) -> dict:
    try:
        ok, raw = _run_helper("today", timeout=20)
        if not ok:
            return _unavailable("Takvim okunamadı: " + str(raw)[:160])
        valid, detail, events = parse_events(raw)
        if not valid:
            return _unavailable("Takvim okunamadı: " + detail[:160])
        remaining = [item for item in events if item["end_ts"] > now.timestamp()]
        upcoming = next((item for item in remaining if item["start_ts"] > now.timestamp()), None)
        next_event = None
        if upcoming:
            start = datetime.fromtimestamp(upcoming["start_ts"], tz=now.tzinfo)
            next_event = {"title": upcoming["title"], "start": start.isoformat(),
                          "minutes_until": max(1, math.ceil((start - now).total_seconds() / 60))}
        return {"status": "ok", "today_count": len(events),
                "remaining_count": len(remaining), "next_today": next_event}
    except Exception:
        return _unavailable("Takvim okunamadı.")


def _reminders() -> dict:
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            all_future = pool.submit(_run_helper, "reminders_list",
                                     payload={"query": "all", "limit": 20,
                                              "list_name": ""}, timeout=25)
            overdue_future = pool.submit(_run_helper, "reminders_list",
                                         payload={"query": "overdue", "limit": 1,
                                                  "list_name": ""}, timeout=25)
            ok, raw = all_future.result()
            overdue_ok, overdue_raw = overdue_future.result()
        if not ok:
            return _unavailable("Anımsatıcılar okunamadı: " + str(raw)[:160])
        valid, detail, items = parse_reminders(raw)
        if not valid:
            return _unavailable("Anımsatıcılar okunamadı: " + detail[:160])
        payload = json.loads(raw)
        total = payload.get("total_count")
        overdue = None
        if overdue_ok:
            overdue_valid, _, overdue_items = parse_reminders(overdue_raw)
            if overdue_valid:
                overdue_payload = json.loads(overdue_raw)
                overdue_total = overdue_payload.get("total_count")
                if type(overdue_total) is int and overdue_total >= len(overdue_items):
                    overdue = overdue_total
                elif len(overdue_items) < 1:
                    overdue = 0
        if type(total) is int and total >= len(items):
            return {"status": "ok" if type(overdue) is int else "partial",
                    "open_count": total,
                    "overdue_count": overdue if type(overdue) is int else None}
        # Older helper binaries can return at most twenty records without a total.
        if len(items) == 20:
            return {"status": "partial", "open_count": None, "at_least": 20,
                    "detail": "Anımsatıcı toplamı 20 kaydın üzerinde olabilir."}
        return {"status": "partial", "open_count": len(items), "overdue_count": None,
                "detail": "Gecikmiş anımsatıcı sayısı doğrulanamadı."}
    except Exception:
        return _unavailable("Anımsatıcılar okunamadı.")


def _downloads(today) -> dict:
    folder = Path.home() / "Downloads"
    count = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                    continue
                created = getattr(entry.stat(follow_symlinks=False), "st_birthtime", None)
                if created is None:
                    return _unavailable("İndirilenler dosyalarının oluşturulma tarihi alınamadı.")
                if datetime.fromtimestamp(created).date() == today:
                    count += 1
        return {"status": "ok", "today_created_count": count,
                "scope": "direct_files", "date_basis": "filesystem_creation"}
    except (OSError, ValueError):
        return _unavailable("İndirilenler klasörü okunamadı.")


def _weather() -> dict:
    try:
        summary = get_weather_summary(day="tomorrow")
        if not isinstance(summary, str) or summary.startswith("Hata:"):
            return _unavailable("Yarınki hava tahmini alınamadı.")
        return {"status": "ok", "period": "tomorrow_daily", "summary": summary}
    except Exception:
        return _unavailable("Yarınki hava tahmini alınamadı.")


def _internet() -> dict:
    try:
        with socket.create_connection(("example.com", 443), timeout=2):
            return {"status": "ok", "reachable": True, "check": "dns_tcp"}
    except OSError:
        # One failed endpoint does not establish that the entire internet is offline.
        return {"status": "partial", "reachable": None,
                "detail": "İnternet erişimi bu kısa bağlantı kontrolüyle doğrulanamadı."}


def _activity() -> dict:
    try:
        if not get_app_config_value("activity_history_enabled", True):
            return {"status": "disabled", "detail": "Etkinlik geçmişi ayarlardan kapalı."}
        if not DATABASE.exists():
            return {"status": "ok", "recent_count": 0, "latest": None}
        payload = json.loads(get_activity_history(period="recent", limit=1))
        if payload.get("status") == "disabled":
            return {"status": "disabled", "detail": "Etkinlik geçmişi ayarlardan kapalı."}
        if payload.get("status") != "ok":
            return _unavailable("Son aktiviteler okunamadı.")
        entries = payload.get("entries") or []
        latest = None
        if entries:
            item = entries[0]
            latest = {"app": str(item.get("app", ""))[:80],
                      "title": str(item.get("title", ""))[:100],
                      "last_seen": str(item.get("last_seen", ""))}
        return {"status": "ok", "recent_count": int(payload.get("count", 0)),
                "latest": latest, "window": "last_2_hours"}
    except (OSError, ValueError, TypeError):
        return _unavailable("Son aktiviteler okunamadı.")


def _warnings(sections: dict) -> list[dict]:
    warnings = []
    def add(code: str, message: str):
        warnings.append({"code": code, "message": message})
    battery = sections["battery"]
    if battery["status"] == "ok" and not battery["plugged"] and battery["percent"] <= 15:
        add("low_battery", f"Pil yüzde {battery['percent']} seviyesinde.")
    cpu = sections["cpu"]
    if cpu["status"] == "ok" and cpu["percent"] >= 85:
        add("high_cpu", f"Anlık CPU kullanımı yüzde {cpu['percent']}.")
    memory = sections["memory"]
    if memory["status"] == "ok" and memory["percent"] >= 90:
        add("high_memory", f"Bellek kullanımı yüzde {memory['percent']}.")
    disk = sections["disk"]
    if disk["status"] == "ok" and disk["used_percent"] >= 90:
        add("low_disk", f"Disk yüzde {disk['used_percent']} dolu.")
    reminders = sections["reminders"]
    if reminders["status"] in ("ok", "partial") and (reminders.get("overdue_count") or 0) > 0:
        add("overdue_reminders", f"{reminders['overdue_count']} gecikmiş anımsatıcı var.")
    calendar = sections["calendar"]
    next_event = calendar.get("next_today") if calendar["status"] == "ok" else None
    if next_event and next_event["minutes_until"] <= 15:
        add("event_soon", f"{next_event['title']} etkinliği {next_event['minutes_until']} dakika sonra.")
    return warnings


def _spoken(sections: dict, warnings: list[dict]) -> str:
    lines = []
    core = ("battery", "cpu", "memory", "disk")
    if not warnings and all(sections[name]["status"] == "ok" for name in core):
        lines.append("Önemli bir sistem uyarısı görünmüyor.")
    battery = sections["battery"]
    if battery["status"] == "ok":
        lines.append(f"Pil yüzde {battery['percent']}.")
        minutes = battery["estimated_minutes_left"]
        if minutes is not None:
            hours, remaining_minutes = divmod(minutes, 60)
            duration = (f"{hours} saat {remaining_minutes} dakika" if hours and remaining_minutes
                        else f"{hours} saat" if hours else f"{remaining_minutes} dakika")
            lines.append(f"Tahmini kalan kullanım süresi {duration}.")
        elif battery["plugged"]:
            lines.append("Şarja bağlıyken kalan kullanım süresi tahmini verilmez.")
        else:
            lines.append("Tahmini kalan kullanım süresi şu an alınamadı.")
    cpu = sections["cpu"]
    if cpu["status"] == "ok":
        lines.append(f"Anlık işlemci kullanımı yüzde {cpu['percent']}.")
    memory = sections["memory"]
    if memory["status"] == "ok":
        lines.append(f"Bellek yüzde {memory['percent']}.")
    disk = sections["disk"]
    if disk["status"] == "ok":
        lines.append(f"Disk yüzde {disk['used_percent']} dolu.")
    calendar = sections["calendar"]
    if calendar["status"] == "ok":
        lines.append(f"Bugün {calendar['remaining_count']} etkinliğin kaldı.")
        next_event = calendar["next_today"]
        if next_event and next_event["minutes_until"] <= 120:
            lines.append(f"Sıradaki {next_event['title']} {next_event['minutes_until']} dakika sonra.")
    reminders = sections["reminders"]
    if reminders["status"] in ("ok", "partial") and reminders.get("open_count") is not None:
        lines.append(f"{reminders['open_count']} açık anımsatıcın var.")
    elif reminders["status"] == "partial" and reminders.get("at_least"):
        lines.append(f"En az {reminders['at_least']} açık anımsatıcın var.")
    weather = sections["weather"]
    if weather["status"] == "ok":
        lines.append(weather["summary"])
    internet = sections["internet"]
    if internet["status"] == "ok":
        lines.append("İnternet erişimi var.")
    activity = sections["activity"]
    if activity["status"] == "ok" and activity["latest"]:
        lines.append(f"Son iki saatte {activity['latest']['app']} kullanılmış.")
    elif activity["status"] == "ok":
        lines.append("Son iki saatte etkinlik kaydı yok.")
    elif activity["status"] == "disabled":
        lines.append("Etkinlik geçmişi kapalı.")
    downloads = sections["downloads"]
    if downloads["status"] == "ok" and downloads["today_created_count"]:
        lines.append(f"Bugün İndirilenler'e {downloads['today_created_count']} dosya eklenmiş.")
    if warnings:
        lines.append("Önemli uyarılar: " + " ".join(item["message"] for item in warnings))
    missing = [name for name, label in (("battery", "pil"), ("cpu", "işlemci"),
               ("memory", "bellek"), ("disk", "disk"), ("calendar", "takvim"),
               ("reminders", "anımsatıcı"), ("weather", "hava"),
               ("internet", "internet"), ("activity", "aktivite"),
               ("downloads", "İndirilenler"))
               if sections[name]["status"] in ("unavailable", "partial")]
    if missing:
        lines.append("Şu bilgileri tam doğrulayamadım: " + ", ".join(missing) + ".")
    return " ".join(lines)


def status_report() -> str:
    """Read independent sources once, in parallel; report failures explicitly."""
    now = datetime.now().astimezone()
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {
            "system": pool.submit(_system),
            "calendar": pool.submit(_calendar, now),
            "reminders": pool.submit(_reminders),
            "downloads": pool.submit(_downloads, now.date()),
            "weather": pool.submit(_weather),
            "internet": pool.submit(_internet),
            "activity": pool.submit(_activity),
        }
        system = futures["system"].result()
        sections = {**system, "calendar": futures["calendar"].result(),
                    "reminders": futures["reminders"].result(),
                    "downloads": futures["downloads"].result(),
                    "weather": futures["weather"].result(),
                    "internet": futures["internet"].result(),
                    "activity": futures["activity"].result()}
    # A successful live weather fetch itself proves network access even if the
    # independent TCP probe failed (for example because that endpoint was blocked).
    if sections["weather"]["status"] == "ok" and sections["internet"]["status"] != "ok":
        sections["internet"] = {"status": "ok", "reachable": True,
                                "check": "weather_service"}
    warnings = _warnings(sections)
    failed = [name for name, item in sections.items() if item["status"] == "unavailable"]
    partial = any(item["status"] == "partial" for item in sections.values())
    return json.dumps({
        "status": "error" if len(failed) == len(sections) else "partial" if failed or partial else "ok",
        "generated_at": now.isoformat(), "sections": sections,
        "failed_sources": failed, "warnings": warnings,
        "spoken_summary": _spoken(sections, warnings),
        "changes_made": False,
    }, ensure_ascii=False)
