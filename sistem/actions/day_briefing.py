"""Read-only day briefing assembled from calendar, weather and reminders."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json

from actions.calendar import get_calendar_events
from actions.reminders import get_reminders
from actions.weather import get_weather_summary


def _read(label: str, function, *args) -> str:
    try:
        return str(function(*args) or "")
    except Exception as exc:
        return f"Hata: {label} okunamadı ({type(exc).__name__})."


def _status(source: str, text: str) -> str:
    # Yalnızca ilk satır (başlık/mesaj) değerlendirilir: etkinlik veya anımsatıcı BAŞLIKLARINDA geçen
    # "görünmüyor"/"okunamadı" sözcükleri kaynağı yanlışlıkla boş/hatalı saydırıyordu.
    low = (text.strip().splitlines() or [""])[0].casefold()
    if (not low.strip() or low.startswith("hata:") or "okunamadi" in low or "okunamadı" in low
            or "erisim izni gerekiyor" in low or "erişim izni gerekiyor" in low):
        return "error"
    if source != "weather" and ("gorunmuyor" in low or "görünmüyor" in low):
        return "empty"
    return "ok"


def prepare_day(day: str = "tomorrow", location: str = "") -> str:
    """Gather independent sources; do not add reminders or assume school events exist."""
    day = str(day or "tomorrow").strip().casefold()
    if day not in {"today", "tomorrow"}:
        return json.dumps({"status": "error", "message": "day today veya tomorrow olmalı."}, ensure_ascii=False)

    target = datetime.now().date() + timedelta(days=1 if day == "tomorrow" else 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        weather_future = pool.submit(_read, "Hava durumu", get_weather_summary,
                                     str(location or "").strip() or None, day)
        calendar_text = _read("Takvim", get_calendar_events, day, 60)
        reminders_text = _read("Anımsatıcılar", get_reminders, day, 20)
        weather_text = weather_future.result()

    sources = {
        "calendar": {"status": _status("calendar", calendar_text), "text": calendar_text},
        "weather": {"status": _status("weather", weather_text), "text": weather_text},
        "reminders": {"status": _status("reminders", reminders_text), "text": reminders_text},
    }
    failed = [name for name, item in sources.items() if item["status"] == "error"]
    return json.dumps({
        "status": "error" if len(failed) == len(sources) else "partial" if failed else "ok",
        "date": target.isoformat(),
        "day": day,
        "sources": sources,
        "failed_sources": failed,
        "changes_made": False,
        "note": "Yalnızca okuma yapıldı. Okul/iş saati takvimde yoksa uydurma. Hatırlatıcı kullanıcı istemeden eklenmedi.",
    }, ensure_ascii=False)
