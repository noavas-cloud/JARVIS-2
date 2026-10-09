"""Read-only evidence for short, situational recommendations."""

from __future__ import annotations

import json
import os
import time
import unicodedata
from pathlib import Path

from actions.status_report import _system


DOWNLOADS = Path.home() / "Downloads"
PARTIAL_SUFFIXES = (".crdownload", ".download", ".part", ".partial")
RECENT_SECONDS = 120


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
    return "".join(char for char in value if not unicodedata.combining(char))


def _pending_downloads(folder: Path = DOWNLOADS, now: float | None = None) -> dict:
    """A partial file is evidence of an unfinished download, not proof of transfer."""
    now = time.time() if now is None else now
    recent = []
    older_count = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.name.lower().endswith(PARTIAL_SUFFIXES) or entry.is_symlink():
                    continue
                age = max(0, now - entry.stat(follow_symlinks=False).st_mtime)
                if age <= RECENT_SECONDS:
                    recent.append({"name": entry.name, "modified_seconds_ago": round(age)})
                else:
                    older_count += 1
    except OSError:
        return {"status": "unavailable", "detail": "İndirilenler klasörü okunamadı."}
    return {"status": "ok", "recent_partial_count": len(recent),
            "recent_partial_files": sorted(recent, key=lambda item: item["modified_seconds_ago"])[:5],
            "older_partial_count": older_count,
            "interpretation": "Yakın zamanda değişen geçici dosya, devam eden indirmeye işaret edebilir; kesin aktarım kanıtı değildir."}


def assess_situation(question: str) -> str:
    """Gather live facts and express bounded implications; never alter the Mac."""
    question = str(question or "").strip()
    if not question:
        return json.dumps({"status": "needs_subject", "message": "Hangi durum hakkında değerlendirme istiyorsun?"},
                          ensure_ascii=False)

    folded = _fold(question)
    demanding = any(word in folded for word in (
        "oyun", "oyna", "game", "gaming", "minecraft", "steam", "render", "yayin", "stream"))
    system = _system()
    downloads = _pending_downloads()
    reasons = []
    limits = []

    battery = system["battery"]
    if battery["status"] == "ok":
        if not battery["plugged"] and battery["percent"] <= 30:
            reasons.append({"code": "low_battery", "fact": f"Pil %{battery['percent']} ve şarja bağlı değil.",
                            "implication": "Uzun veya yoğun bir işe başlamadan şarja takmak daha güvenli."})
    else:
        limits.append("Pil durumu okunamadı.")

    cpu = system["cpu"]
    if cpu["status"] == "ok":
        if cpu["percent"] >= 85:
            reasons.append({"code": "high_cpu", "fact": f"Anlık CPU kullanımı %{cpu['percent']}.",
                            "implication": "Yoğun bir iş başlatılırsa performans düşebilir; bu yalnızca anlık ölçüm."})
    else:
        limits.append("CPU durumu okunamadı.")

    memory = system["memory"]
    if memory["status"] == "ok":
        if memory["percent"] >= 90:
            reasons.append({"code": "high_memory", "fact": f"Bellek kullanımı %{memory['percent']}.",
                            "implication": "Yeni bir yoğun uygulama açmak sistemi zorlayabilir."})
    else:
        limits.append("Bellek durumu okunamadı.")

    disk = system["disk"]
    if disk["status"] == "ok":
        if disk["free_gib"] < 5:
            reasons.append({"code": "low_disk", "fact": f"Diskte {disk['free_gib']} GiB boş yer var.",
                            "implication": "Kurulum veya büyük indirme için alan yetersiz kalabilir."})
    else:
        limits.append("Disk durumu okunamadı.")

    if downloads["status"] == "ok":
        if downloads["recent_partial_count"]:
            reasons.append({"code": "recent_partial_download",
                            "fact": f"İndirilenler'de yakın zamanda değişen {downloads['recent_partial_count']} tamamlanmamış indirme dosyası var.",
                            "implication": "Aktarım sürüyorsa ağ veya disk kullanımıyla çakışabilir; tamamlanmasını beklemek daha sorunsuz olabilir."})
    else:
        limits.append("İndirmeler doğrulanamadı.")

    if demanding:
        codes = {reason["code"] for reason in reasons}
        if codes & {"high_cpu", "high_memory", "low_disk"}:
            recommendation = "Önce sistem yükünü yeniden kontrol etmek veya düşük disk alanını gidermek daha güvenli; şu an sorunsuz çalışacağı söylenemez."
        elif reasons:
            recommendation = "Başlamak mümkün olabilir; pil ve olası indirme koşulları nedeniyle önce ilgili önlemleri almak daha sorunsuz olabilir."
        elif limits:
            recommendation = "Ölçümler eksik olduğu için şu an sorunsuz çalışacağı söylenemez."
        else:
            recommendation = "Şu anki ölçümlerde belirgin bir sistem engeli görünmüyor; sorunsuz çalışacağı garanti edilemez."
        limits.append("Uygulamanın sistem gereksinimleri ve gelecekteki performansı bu ölçümlerle doğrulanamaz.")
    else:
        recommendation = "Bu verileri sorudaki kararla ilişkilendir; ilgisiz ölçümleri gerekçe yapma."

    return json.dumps({"status": "ok", "question": question,
                       "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                       "system": system, "downloads": downloads,
                       "considerations": reasons, "recommendation": recommendation,
                       "limitations": limits, "changes_made": False}, ensure_ascii=False)
