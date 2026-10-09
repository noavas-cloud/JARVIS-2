"""Read-only what-if calculations for files and running applications."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import heapq
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import unicodedata

import psutil

from actions.file_management import sources_of
from actions.performance_diagnosis import _app_name


GIB = 1024 ** 3
MIB = 1024 ** 2
MAX_ENTRIES = 100_000
MAX_SECONDS = 12
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".wmv", ".hevc"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".gif", ".psd", ".raw"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt"}


def _reply(status: str, **fields) -> str:
    return json.dumps({"status": status, "changes_made": False, **fields}, ensure_ascii=False)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    return "".join(char for char in value if not unicodedata.combining(char))


def _size(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.1f} GiB"
    return f"{value / MIB:.0f} MiB"


def _category(path: Path) -> str:
    if any(_fold(part) in {"cache", "caches", ".cache", "onbellek"} for part in path.parts):
        return "cache"
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    return "other"


def _last_used(path: Path) -> str | None:
    """Spotlight metadata is optional; modification/access times are not proof of use."""
    try:
        result = subprocess.run(["/usr/bin/mdls", "-raw", "-name", "kMDItemLastUsedDate", str(path)],
                                capture_output=True, text=True, timeout=1)
        if result.returncode:
            return None
        match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}", result.stdout)
        if match:
            return datetime.strptime(match.group(), "%Y-%m-%d %H:%M:%S %z").isoformat()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _simulate_trash(path: str) -> str:
    try:
        source = sources_of([path])[0]  # Same target validation as the real trash tool.
        root_info = source.lstat()
    except (OSError, TypeError, ValueError) as exc:
        return _reply("error", message=str(exc))
    if stat.S_ISLNK(root_info.st_mode):
        return _reply("ok", kind="trash_path", path=str(source), target_type="symlink",
                      immediate_free_bytes=0, potential_after_emptying_bytes=0,
                      spoken_summary="Bu bir sembolik bağlantı. Çöp Sepeti'ne taşınırsa hedef klasörün içeriği silinmez; anlamlı bir disk alanı kazanımı beklenmez.")
    if not (stat.S_ISDIR(root_info.st_mode) or stat.S_ISREG(root_info.st_mode)):
        return _reply("unsupported", kind="trash_path", message="Bu öğenin türü için güvenilir bir simülasyon yapamıyorum.")

    started = time.monotonic()
    now = time.time()
    stack = [source]
    entries = files = folders = symlinks = inaccessible = 0
    logical = 0
    recent_modified = recent_bytes = 0
    categories = Counter()
    old_categories = Counter()
    inodes = {}  # (device, inode) -> allocated bytes, link count, links inside target
    top_files = []
    truncated = False
    while stack:
        if entries >= MAX_ENTRIES or time.monotonic() - started >= MAX_SECONDS:
            truncated = True
            break
        current = stack.pop()
        try:
            info = current.lstat()
        except OSError:
            inaccessible += 1
            continue
        entries += 1
        if stat.S_ISLNK(info.st_mode):
            symlinks += 1
            continue
        if stat.S_ISDIR(info.st_mode):
            folders += 1
            try:
                with os.scandir(current) as iterator:
                    for item in iterator:
                        stack.append(Path(item.path))
            except OSError:
                inaccessible += 1
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        files += 1
        logical += info.st_size
        category = _category(current)
        categories[category] += info.st_size
        if now - info.st_mtime <= 30 * 86400:
            recent_modified += 1
            recent_bytes += info.st_size
        else:
            old_categories[category] += info.st_size
        key = (info.st_dev, info.st_ino)
        blocks = max(0, getattr(info, "st_blocks", 0)) * 512
        if key in inodes:
            prior_blocks, links, inside = inodes[key]
            inodes[key] = (prior_blocks, links, inside + 1)
        else:
            inodes[key] = (blocks, info.st_nlink, 1)
        if len(top_files) < 8:
            heapq.heappush(top_files, (info.st_size, str(current)))
        elif info.st_size > top_files[0][0]:
            heapq.heapreplace(top_files, (info.st_size, str(current)))

    allocated = sum(blocks for blocks, _, _ in inodes.values())
    potential = sum(blocks for blocks, links, inside in inodes.values() if inside >= links)
    outside_hardlinks = sum(1 for _, links, inside in inodes.values() if inside < links)
    biggest = []
    for size, name in sorted(top_files, reverse=True):
        used = _last_used(Path(name))
        biggest.append({"path": name, "logical_bytes": size, "last_used_spotlight": used})
    recent_used = [item for item in biggest if item["last_used_spotlight"]
                   and 0 <= now - datetime.fromisoformat(item["last_used_spotlight"]).timestamp() <= 30 * 86400]
    complete = not truncated and inaccessible == 0
    summary = (f"{files} dosya ve {folders} klasör taradım. "
               f"Dosyaların mantıksal toplamı {_size(logical)}; ölçülen ayrılmış alan yaklaşık {_size(allocated)}. "
               "Çöp Sepeti'ne taşımak hemen yer açmaz. "
               f"Sepet daha sonra boşaltılırsa en fazla yaklaşık {_size(potential)} alan açılabilir; "
               "APFS klonları ve anlık görüntüler nedeniyle gerçek kazanç daha düşük olabilir. "
               f"Son 30 günde değiştirilen {recent_modified} dosya var; değiştirilme tarihi kullanıldığı anlamına gelmez.")
    if not complete:
        summary = "Tarama tamamlanmadı; sayılar yalnızca incelenen bölümü gösteriyor. " + summary
    return _reply("ok" if complete else "partial", kind="trash_path", path=str(source),
                  target_type="folder" if stat.S_ISDIR(root_info.st_mode) else "file",
                  entries_scanned=entries, file_count=files, folder_count=folders,
                  symlink_count=symlinks, inaccessible_count=inaccessible, truncated=truncated,
                  logical_bytes=logical, allocated_bytes_estimate=allocated,
                  immediate_free_bytes=0, potential_after_emptying_bytes=potential,
                  outside_hardlink_groups=outside_hardlinks,
                  categories_logical_bytes=dict(categories),
                  older_than_30_days_modified_bytes=dict(old_categories),
                  recently_modified_count=recent_modified, recently_modified_bytes=recent_bytes,
                  largest_files=biggest, recently_used_spotlight_in_largest_files=recent_used,
                  last_used_scope="Spotlight son kullanım tarihi yalnızca en büyük 8 dosyada sorgulandı; boş değer kullanılmadığı anlamına gelmez.",
                  spoken_summary=summary)


def _app_target(value: str) -> str:
    name = _fold(value).strip()
    return name[:-4] if name.endswith(".app") else name


def _simulate_quit(app_names) -> str:
    if isinstance(app_names, str):
        app_names = [app_names]
    if not isinstance(app_names, list) or not 1 <= len(app_names) <= 8:
        return _reply("needs_target", kind="quit_apps", message="Simüle edilecek 1–8 uygulama adı belirt.")
    requested = [_app_target(name) for name in app_names]
    if any(not name for name in requested):
        return _reply("needs_target", kind="quit_apps", message="Uygulama adı boş olamaz.")
    requested = list(dict.fromkeys(requested))
    matches = []
    inaccessible = 0
    try:
        processes = list(psutil.process_iter())
    except (psutil.Error, OSError):
        return _reply("unavailable", kind="quit_apps", message="Çalışan uygulamalar okunamadı.")
    for process in processes:
        try:
            name = _app_name(process)
            if _app_target(name) not in requested:
                continue
            started = process.create_time()
            times = process.cpu_times()
            rss = process.memory_info().rss
            matches.append((process, name, started, times.user + times.system, rss))
        except (psutil.Error, OSError, ValueError, TypeError):
            inaccessible += 1
    if not matches:
        return _reply("no_match", kind="quit_apps", requested_apps=app_names,
                      message="Belirtilen uygulamaları çalışan süreçler arasında doğrulayamadım.")

    psutil.cpu_percent(interval=None)
    sample_start = time.monotonic()
    time.sleep(0.8)
    elapsed = max(0.001, time.monotonic() - sample_start)
    system_cpu = psutil.cpu_percent(interval=None)
    logical_cores = psutil.cpu_count() or 1
    grouped = {}
    for process, name, started, previous_cpu, rss in matches:
        try:
            if process.create_time() != started:
                continue
            times = process.cpu_times()
            delta = max(0, times.user + times.system - previous_cpu)
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
        item = grouped.setdefault(name, {"app": name, "processes": 0,
                                         "cpu_one_core_percent": 0.0, "rss_bytes_approx": 0})
        item["processes"] += 1
        item["cpu_one_core_percent"] += 100 * delta / elapsed
        item["rss_bytes_approx"] += rss
    if not grouped:
        return _reply("unavailable", kind="quit_apps", message="Ölçüm sırasında uygulama süreçleri kapanmış veya okunamamış.")
    apps = list(grouped.values())
    for item in apps:
        item["cpu_one_core_percent"] = round(item["cpu_one_core_percent"], 1)
    found = {_app_target(name) for name in grouped}
    missing = [name for name in app_names if _app_target(name) not in found]
    selected_cpu = sum(item["cpu_one_core_percent"] for item in apps)
    total_rss = sum(item["rss_bytes_approx"] for item in apps)
    try:
        battery = psutil.sensors_battery()
    except (psutil.Error, OSError):
        battery = None
    battery_now = None if battery is None else {"percent": round(battery.percent),
                                                "plugged": bool(battery.power_plugged),
                                                "system_minutes_left_estimate": round(battery.secsleft / 60)
                                                if not battery.power_plugged and 0 < battery.secsleft < 86400 else None}
    summary = (f"Seçtiğin uygulamalar şu an yaklaşık bir mantıksal çekirdeğin yüzde {selected_cpu:.1f} kadarı CPU "
               f"ve yaklaşık {_size(total_rss)} süreç belleği kullanıyor. Kapatılırsa bu anlık yük azalabilir; "
               "paylaşılan bellek nedeniyle tamamı boşalmayabilir. Pil süresine kaç dakika ekleneceğini "
               "yalnızca bu kısa CPU ölçümünden güvenilir biçimde hesaplayamam.")
    if battery_now and battery_now["plugged"]:
        summary += " Mac şu an şarjda; pil tüketim farkı doğrudan ölçülemiyor."
    if missing:
        summary += " Bazı uygulamalar bulunamadı: " + ", ".join(missing[:4]) + "."
    return _reply("partial" if missing or inaccessible else "ok", kind="quit_apps",
                  requested_apps=app_names, measured_apps=apps, missing_apps=missing,
                  inaccessible_processes=inaccessible, sample_seconds=round(elapsed, 2),
                  selected_cpu_one_core_percent=round(selected_cpu, 1),
                  system_cpu_percent=round(system_cpu, 1), logical_cores=logical_cores,
                  rss_bytes_approx=total_rss, battery_now=battery_now,
                  battery_minutes_gained_estimate=None,
                  limitation="Süreç CPU kullanımı güç tüketimi değildir; ekran, GPU, ağ ve arka plan işleri ölçülmedi. Uygulama kapandıktan sonraki pil süresi bu verilerden hesaplanamaz.",
                  spoken_summary=summary)


def simulate_action(action: str, path: str = "", app_names=None) -> str:
    """Simulate consequences without trashing files or quitting processes."""
    action = str(action or "").strip()
    if action == "trash_path":
        if not path:
            return _reply("needs_target", kind="trash_path", message="Simüle edilecek kesin dosya veya klasör yolunu belirt.")
        return _simulate_trash(path)
    if action == "quit_apps":
        return _simulate_quit(app_names)
    return _reply("unsupported", message="Şimdilik dosyayı/klasörü Çöp Sepeti'ne taşıma ve uygulama kapatma sonuçlarını simüle edebiliyorum.")
