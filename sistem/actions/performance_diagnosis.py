"""Read-only, short-window performance diagnosis for macOS."""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import time

import psutil


SAMPLE_SECONDS = 0.65
GIB = 1024 ** 3
MIB = 1024 ** 2


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "detail": reason}


def _app_name(process) -> str:
    if process.pid == os.getpid():
        return "JARVIS"
    try:
        for component in Path(process.exe()).parts:
            if component.lower().endswith(".app"):
                return component[:-4]
    except (psutil.Error, OSError, ValueError):
        pass
    try:
        name = process.name().strip() or f"PID {process.pid}"
    except (psutil.Error, OSError):
        return f"PID {process.pid}"
    helper = name.find(" Helper")
    return name[:helper] if helper > 0 else name


def _counters():
    try:
        disk = psutil.disk_io_counters()
    except (psutil.Error, OSError, AttributeError):
        disk = None
    try:
        swap = psutil.swap_memory()
    except (psutil.Error, OSError, AttributeError):
        swap = None
    return disk, swap


def _processes(processes) -> dict:
    grouped = {}
    inaccessible = 0
    for process in processes:
        try:
            cpu = float(process.cpu_percent(interval=None))
            rss = int(process.memory_info().rss)
            if not math.isfinite(cpu) or cpu < 0 or rss < 0:
                continue
            name = _app_name(process)
        except (psutil.Error, OSError, ValueError, TypeError):
            inaccessible += 1
            continue
        item = grouped.setdefault(name, {"app": name, "cpu_one_core_percent": 0.0,
                                         "rss_bytes": 0, "process_count": 0})
        item["cpu_one_core_percent"] += cpu
        item["rss_bytes"] += rss
        item["process_count"] += 1
    if not grouped:
        return _unavailable("Çalışan uygulamaların kaynak kullanımı okunamadı.")
    def formatted(item):
        return {"app": item["app"], "cpu_one_core_percent": round(item["cpu_one_core_percent"], 1),
                "rss_gib_approx": round(item["rss_bytes"] / GIB, 2),
                "process_count": item["process_count"]}
    by_cpu = sorted(grouped.values(), key=lambda item: item["cpu_one_core_percent"], reverse=True)
    by_memory = sorted(grouped.values(), key=lambda item: item["rss_bytes"], reverse=True)
    return {"status": "ok", "top_cpu": [formatted(item) for item in by_cpu[:3]],
            "top_memory": [formatted(item) for item in by_memory[:3]],
            "observed_processes": sum(item["process_count"] for item in grouped.values()),
            "inaccessible_processes": inaccessible,
            "note": "CPU yüzdesi tek mantıksal çekirdeğe göredir; uygulamanın yardımcı süreçleri birleştirildi. Bellek RSS toplamı yaklaşık değerdir."}


def _collect() -> dict:
    try:
        processes = list(psutil.process_iter())
    except (psutil.Error, OSError):
        processes = []
    for process in processes:
        try:
            process.cpu_percent(interval=None)
        except (psutil.Error, OSError):
            pass
    disk_before, swap_before = _counters()
    start = time.monotonic()
    try:
        cpu_percent = psutil.cpu_percent(interval=SAMPLE_SECONDS)
        cpu = {"status": "ok", "percent": round(cpu_percent, 1)}
    except (psutil.Error, OSError, ValueError):
        time.sleep(SAMPLE_SECONDS)
        cpu = _unavailable("CPU kullanımı ölçülemedi.")
    elapsed = max(0.001, time.monotonic() - start)
    disk_after, swap_after = _counters()

    try:
        memory = psutil.virtual_memory()
        memory_result = {"status": "ok", "percent": round(memory.percent, 1),
                         "available_gib": round(memory.available / GIB, 1),
                         "total_gib": round(memory.total / GIB, 1)}
        if swap_before is not None and swap_after is not None:
            memory_result["swap_out_mib_per_second"] = round(
                max(0, swap_after.sout - swap_before.sout) / MIB / elapsed, 1)
    except (psutil.Error, OSError, ValueError, AttributeError):
        memory_result = _unavailable("Bellek kullanımı okunamadı.")

    try:
        usage = psutil.disk_usage("/")
        disk_result = {"status": "ok", "free_gib": round(usage.free / GIB, 1),
                       "free_percent": round(100 * usage.free / max(1, usage.total), 1)}
        if disk_before is not None and disk_after is not None:
            disk_result["read_mib_per_second"] = round(
                max(0, disk_after.read_bytes - disk_before.read_bytes) / MIB / elapsed, 1)
            disk_result["write_mib_per_second"] = round(
                max(0, disk_after.write_bytes - disk_before.write_bytes) / MIB / elapsed, 1)
    except (psutil.Error, OSError, ValueError, AttributeError):
        disk_result = _unavailable("Disk durumu okunamadı.")

    return {"cpu": cpu, "memory": memory_result, "disk": disk_result,
            "applications": _processes(processes), "sample_seconds": round(elapsed, 2)}


def _summary(sections: dict) -> tuple[str, list[str]]:
    cpu, memory, disk, apps = (sections[name] for name in ("cpu", "memory", "disk", "applications"))
    lines = []
    causes = []
    if cpu["status"] == "ok":
        lines.append(f"Toplam CPU kullanımı yüzde {cpu['percent']}.")
        if cpu["percent"] >= 70:
            causes.append("cpu_high")
            lines.append("İşlemci yükü yüksek.")
    else:
        lines.append("CPU kullanımını ölçemedim.")

    if memory["status"] == "ok":
        lines.append(f"Bellek kullanımı yüzde {memory['percent']}.")
        if memory["percent"] >= 85:
            causes.append("memory_high")
            if memory.get("swap_out_mib_per_second", 0) > 0:
                lines.append("Bellek baskısıyla diske veri taşınıyor.")
            else:
                lines.append("Bellek kullanımı yüksek.")
    else:
        lines.append("Bellek kullanımını okuyamadım.")

    if disk["status"] == "ok":
        lines.append(f"Diskte {disk['free_gib']} gigabayt boş alan var.")
        if disk["free_percent"] < 10 or disk["free_gib"] < 10:
            causes.append("disk_low_space")
            lines.append("Boş alan azalmış; bu performansı etkileyebilir.")
    else:
        lines.append("Disk durumunu okuyamadım.")

    if apps["status"] == "ok":
        top_cpu = apps["top_cpu"][0]
        top_memory = apps["top_memory"][0]
        if top_cpu["cpu_one_core_percent"] >= 10:
            lines.append(f"Ölçümde en çok CPU kullanan {top_cpu['app']}: bir çekirdeğe göre yaklaşık yüzde {top_cpu['cpu_one_core_percent']}.")
        if "memory_high" in causes and top_memory["rss_gib_approx"] >= 1:
            lines.append(f"En çok bellek kullanan {top_memory['app']}, yaklaşık {top_memory['rss_gib_approx']} gigabayt.")
    else:
        lines.append("Hangi uygulamanın kaynak kullandığını göremedim.")

    if not causes:
        lines.append("Bu kısa ölçümde kesin bir yavaşlık nedeni görünmüyor; yavaşlama sırasında tekrar ölçebiliriz.")
    else:
        lines.append("Bu bulgular olası nedenleri gösterir; tek ölçüm kesin tanı koymaz.")
    return " ".join(lines), causes


def _options(sections: dict, causes: list[str]) -> list[dict]:
    """Offer reversible next steps supported by the measured symptom."""
    if not causes:
        return []
    apps = sections["applications"]
    top_cpu = apps["top_cpu"][0] if apps["status"] == "ok" and apps["top_cpu"] else None
    top_memory = apps["top_memory"][0] if apps["status"] == "ok" and apps["top_memory"] else None
    if "cpu_high" in causes:
        app = top_cpu["app"] if top_cpu and top_cpu["cpu_one_core_percent"] >= 10 else None
        return [
            {"number": 1, "action": (f"{app} içindeki yoğun işi incele" if app else "İşlemciyi kullanan uygulamaları yeniden ölç"),
             "consequence": "Açık çalışma korunur; neden daha netleşir.", "recommended": True},
            {"number": 2, "action": (f"Çalışmayı kaydedip {app} uygulamasını yeniden başlat" if app else "Kullanmadığın yoğun uygulamaları belirle"),
             "consequence": "Geçici rahatlama sağlayabilir; açık işler kesilebilir.", "recommended": False},
            {"number": 3, "action": (f"{app} eklentilerini ve güncellemelerini incele" if app else "Bellek, disk ve güncelleme durumunu incele"),
             "consequence": "Daha kalıcı nedeni gösterebilir; daha uzun sürer.", "recommended": False},
        ]
    if "memory_high" in causes:
        app = top_memory["app"] if top_memory and top_memory["rss_gib_approx"] >= 1 else None
        return [
            {"number": 1, "action": (f"{app} bellek kullanımını incele" if app else "Bellek kullanan uygulamaları incele"),
             "consequence": "Hiçbir uygulama kapanmaz.", "recommended": True},
            {"number": 2, "action": "Kullanmadığın sekme ve uygulamaları seçerek kapat",
             "consequence": "Bellek boşalabilir; kaydedilmemiş işler etkilenebilir.", "recommended": False},
            {"number": 3, "action": "Bellek baskısı ve başlangıç uygulamalarını incele",
             "consequence": "Tekrarlayan sorunun nedenini bulmaya yardım eder; zaman alır.", "recommended": False},
        ]
    return [
        {"number": 1, "action": "Büyük dosyaları salt okunur olarak bul",
         "consequence": "Hiçbir dosya değişmez; boş alanı neyin kullandığı görülür.", "recommended": True},
        {"number": 2, "action": "Gereksiz dosyaları sen seçtikten sonra Çöp Sepeti'ne taşı",
         "consequence": "Alan açılabilir; seçilen dosyalar taşınır.", "recommended": False},
        {"number": 3, "action": "Depolama ayarları ve uygulama verilerini incele",
         "consequence": "Daha ayrıntılıdır; hiçbir şey otomatik silinmez.", "recommended": False},
    ]


def diagnose_slow_mac() -> str:
    """Describe observed pressure and top apps without claiming unmeasured causes."""
    sections = _collect()
    summary, causes = _summary(sections)
    options = _options(sections, causes)
    if options:
        summary += " Üç seçenek var: " + " ".join(
            f"{item['number']}: {item['action']}; {item['consequence']}"
            for item in options
        ) + " İlk seçenek en az müdahale gerektiriyor. Hangisini istersin?"
    missing = [name for name in ("cpu", "memory", "disk", "applications")
               if sections[name]["status"] != "ok"]
    return json.dumps({
        "status": "error" if len(missing) == 4 else "partial" if missing else "ok",
        "measured_at": datetime.now().astimezone().isoformat(),
        "sample_seconds": sections["sample_seconds"],
        "sections": {name: sections[name] for name in ("cpu", "memory", "disk", "applications")},
        "possible_causes": causes, "options": options, "unavailable_sections": missing,
        "spoken_summary": summary, "changes_made": False,
    }, ensure_ascii=False)
