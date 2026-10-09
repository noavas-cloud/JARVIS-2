"""Configurable, deduplicated notifications. No model-generated facts or actions."""
import datetime as dt
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

import psutil

from app_config import load_app_config
from actions.calendar import _run_helper
from actions.performance_diagnosis import _app_name

DEFAULTS = dict(
    enabled=True, spoken=True, battery=True, calendar=True, cpu=True, reminders=True,
    disk_forecast=True, calendar_conflicts=True,
    battery_percent=15, cpu_percent=90, cpu_seconds=600, event_minutes=10,
    interval_minutes=5, quiet_enabled=True, quiet_start=23, quiet_end=8,
)

GIB = 1024 ** 3
_tomorrow_cache = {"date": None, "checked": 0.0, "events": []}


def settings(raw=None):
    raw = load_app_config().get("proactive", {}) if raw is None else raw
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key not in cfg:
                continue
            if isinstance(cfg[key], bool):
                if isinstance(value, bool):
                    cfg[key] = value
            else:
                try:
                    cfg[key] = int(value)
                except (TypeError, ValueError):
                    pass
    for key, low, high in (
        ("battery_percent", 5, 50), ("cpu_percent", 50, 100),
        ("cpu_seconds", 120, 1800), ("event_minutes", 1, 60),
        ("interval_minutes", 1, 180), ("quiet_start", 0, 23), ("quiet_end", 0, 23),
    ):
        cfg[key] = max(low, min(high, cfg[key]))
    return cfg


def quiet(cfg, now):
    if not cfg["quiet_enabled"] or cfg["quiet_start"] == cfg["quiet_end"]:
        return False
    start, end, hour = cfg["quiet_start"], cfg["quiet_end"], now.hour
    return start <= hour < end if start < end else hour >= start or hour < end


def _key(kind, values):
    return kind + ":" + hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]


def read_items(mode, payload, field):
    ok, raw = _run_helper(mode, payload=payload, timeout=25)
    if not ok:
        raise RuntimeError(field + " okunamadı")
    result = json.loads(raw)
    if not result.get("ok"):
        raise RuntimeError(field + " erişim izni veya veri alınamadı")
    return result.get(field, [])


def snapshot(cfg, now):
    result = {"errors": []}
    if cfg["disk_forecast"]:
        try:
            usage = psutil.disk_usage("/")
            result["disk"] = {"free": usage.free, "total": usage.total}
        except (psutil.Error, OSError):
            result["errors"].append("Disk alanı okunamadı.")
    if cfg["battery"]:
        battery = psutil.sensors_battery()
        result["battery"] = None if battery is None else (battery.percent, battery.power_plugged)
    if cfg["cpu"]:
        result["cpu"] = psutil.cpu_percent(interval=0.2)
        processes = {}
        try:
            for process in psutil.process_iter():
                try:
                    times = process.cpu_times()
                    processes[process.pid] = (process.create_time(),
                                              times.user + times.system, _app_name(process))
                except (psutil.Error, OSError, ValueError, TypeError):
                    continue
        except (psutil.Error, OSError):
            pass
        result["processes"] = processes
    if cfg["calendar"]:
        try:
            # Takvim yardımcısı saniye kesirli, saat dilimsiz tarihi okuyamıyordu ("invalid_range"):
            # yaklaşan etkinlik uyarıları hiç çalışmıyordu. Mikrosaniyeler atılır.
            start = now.replace(microsecond=0)
            result["events"] = read_items("range", {
                "start_iso": start.isoformat(),
                "end_iso": (start + dt.timedelta(minutes=cfg["event_minutes"])).isoformat(),
            }, "events")
        except Exception:
            result["errors"].append("Takvim okunamadı; Takvim erişim iznini kontrol et.")
    if cfg["calendar_conflicts"]:
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        cache_age = time.monotonic() - _tomorrow_cache["checked"]
        try:
            if _tomorrow_cache["date"] != tomorrow.date() or not 0 <= cache_age < 900:
                events = read_items("range", {
                    "start_iso": tomorrow.isoformat(),
                    "end_iso": (tomorrow + dt.timedelta(days=1)).isoformat(),
                }, "events")
                _tomorrow_cache.update(date=tomorrow.date(), checked=time.monotonic(), events=events)
            result["tomorrow_events"] = _tomorrow_cache["events"]
        except Exception:
            result["errors"].append("Yarınki takvim okunamadı; Takvim erişim iznini kontrol et.")
    if cfg["reminders"]:
        try:
            result["reminders"] = read_items("reminders_list", {"query": "tomorrow", "limit": 20}, "reminders")
        except Exception:
            result["errors"].append("Hatırlatıcılar okunamadı; Anımsatıcılar erişim iznini kontrol et.")
    return result


def tomorrow_conflicts(items, tomorrow):
    """Return real timed overlaps; touching edges and all-day entries do not conflict."""
    day_start = dt.datetime.combine(tomorrow, dt.time.min).timestamp()
    day_end = dt.datetime.combine(tomorrow + dt.timedelta(days=1), dt.time.min).timestamp()
    events = []
    unique = set()
    for item in items:
        if not isinstance(item, dict) or item.get("all_day"):
            continue
        try:
            start, end = float(item["start_ts"]), float(item["end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end) and start < end and start < day_end and end > day_start):
            continue
        title = " ".join(str(item.get("title") or "İsimsiz etkinlik").split())[:100]
        identity = (start, end, title, str(item.get("calendar") or ""))
        if identity in unique:
            continue
        unique.add(identity)
        events.append((max(start, day_start), min(end, day_end), title))
    events.sort()
    conflicts = []
    for index, (start, end, title) in enumerate(events):
        for next_start, next_end, next_title in events[index + 1:]:
            if next_start >= end:
                break
            if title == next_title and start == next_start and end == next_end:
                continue
            clock = dt.datetime.fromtimestamp(max(start, next_start)).strftime("%H:%M")
            conflicts.append((clock, title, next_title))
    return conflicts


class ProactiveMonitor:
    def __init__(self, state_path=None):
        self.state_path = Path(state_path) if state_path else Path(__file__).resolve().parents[1] / "config/proactive_state.json"
        self.seen = {}
        self.last_notice = 0
        self.cpu_since = None
        self.last_cpu_sample = None
        self.previous_processes = None
        self.high_samples = 0
        self.high_apps = []
        self.advice_by_key = {}
        self.latest_advice = None
        self.disk_samples = []
        try:
            state = json.loads(self.state_path.read_text())
            self.seen = {str(k): float(v) for k, v in state.get("seen", {}).items()}
            self.last_notice = float(state.get("last_notice", 0))
            self.latest_advice = state.get("latest_advice")
            for sample in state.get("disk_samples", []):
                stamp, free, total = (float(value) for value in sample)
                if math.isfinite(stamp) and 0 <= free <= total and total > 0:
                    self.disk_samples.append((stamp, free, total))
            self.disk_samples = self.disk_samples[-336:]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"seen": self.seen, "last_notice": self.last_notice,
                                         "latest_advice": self.latest_advice,
                                         "disk_samples": self.disk_samples}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def _observe_disk(self, disk, ts):
        if not isinstance(disk, dict):
            return
        try:
            free, total = float(disk["free"]), float(disk["total"])
        except (KeyError, TypeError, ValueError):
            return
        if not (math.isfinite(free) and math.isfinite(total) and 0 <= free <= total and total > 0):
            return
        self.disk_samples = [sample for sample in self.disk_samples
                             if ts - 7 * 86400 <= sample[0] <= ts and sample[2] == total]
        if self.disk_samples and ts - self.disk_samples[-1][0] < 1800:
            return
        self.disk_samples.append((ts, free, total))
        self.disk_samples = self.disk_samples[-336:]
        try:
            self._save_state()
        except OSError:
            pass  # Keep the in-memory measurements if persistence is unavailable.

    def _disk_notice(self, disk):
        samples = self.disk_samples
        if not samples or not isinstance(disk, dict):
            return None
        total = samples[-1][2]
        try:
            current_free = float(disk["free"])
            current_total = float(disk["total"])
        except (KeyError, TypeError, ValueError):
            return None
        if current_total != total or not math.isfinite(current_free) or not 0 <= current_free <= total:
            return None
        critical = max(10 * GIB, 0.05 * total)
        if current_free <= critical:
            return ("disk_low", f"Diskte yalnızca {current_free / GIB:.0f} GiB boş alan kaldı. Büyük dosyaları incelememi ister misin?", 86400)
        if len(samples) < 8 or samples[-1][0] - samples[0][0] < 24 * 3600:
            return None
        # Four time bands must all decline. One download or APFS fluctuation is not a trend.
        first_time, last_time = samples[0][0], samples[-1][0]
        band_width = (last_time - first_time) / 4
        bands = [[] for _ in range(4)]
        for stamp, free, _ in samples:
            index = min(3, int((stamp - first_time) / band_width))
            bands[index].append(free)
        if any(len(band) < 2 for band in bands):
            return None
        levels = [statistics.median(band) for band in bands]
        total_drop = levels[0] - levels[-1]
        if total_drop < 2 * GIB or any(levels[index] - levels[index + 1] < 0.1 * total_drop for index in range(3)):
            return None
        if current_free > levels[-1] + 0.5 * GIB:
            return None
        # Estimate from band centres, rather than a single latest free-space reading.
        days_observed = (last_time - first_time) / 86400 * 0.75
        drop_per_day = total_drop / days_observed
        days_left = (current_free - critical) / drop_per_day
        if not 0 < days_left <= 7:
            return None
        approx_days = max(1, round(days_left))
        text = (f"Diskteki boş alan son ölçümlerde düzenli olarak azaldı. "
                f"Bu hız sürerse yaklaşık {approx_days} gün içinde kritik seviye olan "
                f"{critical / GIB:.0f} GiB boş alana düşebilir. Büyük dosyaları incelememi ister misin?")
        return ("disk_forecast", text, 86400)

    def reset_cpu(self):
        self.cpu_since = None
        self.last_cpu_sample = None
        self.previous_processes = None
        self.high_samples = 0
        self.high_apps = []

    def _top_cpu_app(self, processes, monotonic):
        previous = self.previous_processes
        elapsed = monotonic - self.last_cpu_sample if self.last_cpu_sample is not None else 0
        self.previous_processes = processes if isinstance(processes, dict) else None
        if not previous or not self.previous_processes or not 0 < elapsed <= 90:
            return None
        usage = {}
        for pid, (created, total, app) in self.previous_processes.items():
            old = previous.get(pid)
            if old is None or old[0] != created or total < old[1] or not app:
                continue
            usage[app] = usage.get(app, 0.0) + 100 * (total - old[1]) / elapsed
        if not usage:
            return None
        app, percent = max(usage.items(), key=lambda item: item[1])
        return app if percent >= 25 else None

    def _sustained_app(self):
        counts = {}
        for app in self.high_apps:
            if app:
                counts[app] = counts.get(app, 0) + 1
        if not counts:
            return None
        app, count = max(counts.items(), key=lambda item: item[1])
        return app if count >= 5 and count >= 0.8 * max(1, self.high_samples - 1) else None

    def _cpu_advice(self, cpu, now, monotonic):
        app = self._sustained_app()
        minutes = max(1, round((monotonic - self.cpu_since) / 60))
        if app:
            options = [
                {"number": 1, "action": f"{app} içindeki yoğun işi incele",
                 "consequence": "Açık çalışma korunur; önce nedeni anlamaya yarar.", "recommended": True},
                {"number": 2, "action": f"Çalışmayı kaydedip {app} uygulamasını yeniden başlat",
                 "consequence": "Geçici yük düzelebilir; açık işler kısa süre kesilir.", "recommended": False},
                {"number": 3, "action": f"{app} eklentilerini ve güncellemelerini incele",
                 "consequence": "Kalıcı nedeni bulmaya yardım edebilir; daha uzun sürer.", "recommended": False},
            ]
            observation = f"Bu sürede {app} uygulaması ölçümlerde sürekli öne çıktı."
        else:
            options = [
                {"number": 1, "action": "İşlemciyi kullanan uygulamaları ayrıntılı ölç",
                 "consequence": "Açık çalışma korunur; kaynağı belirlemeye yarar.", "recommended": True},
                {"number": 2, "action": "Kullanmadığın yoğun uygulamaları belirle",
                 "consequence": "Kapatmaya karar verirsen açık işler etkilenebilir.", "recommended": False},
                {"number": 3, "action": "Bellek, disk ve güncelleme durumunu incele",
                 "consequence": "Başka bir nedeni ortaya çıkarabilir; daha uzun sürer.", "recommended": False},
            ]
            observation = "Tek bir uygulamanın bütün bu süre boyunca sorumlu olduğunu doğrulayamadım."
        text = (f"Bir şey dikkatimi çekti. Son {minutes} dakikadaki düzenli ölçümlerde işlemci yükü yüksek; "
                f"şu anda yüzde {round(cpu)}. {observation} "
                f"Birinci seçenek: {options[0]['action']}; {options[0]['consequence']} "
                f"İkinci seçenek: {options[1]['action']}; {options[1]['consequence']} "
                f"Üçüncü seçenek: {options[2]['action']}; {options[2]['consequence']} "
                "İlk seçenek en az müdahale gerektiriyor. Hangisini istersin?")
        advice = {"kind": "sustained_cpu", "app": app, "observed_minutes": minutes,
                  "observed_samples": self.high_samples, "cpu_percent_now": round(cpu),
                  "created_at": now.isoformat(),
                  "expires_at": (now + dt.timedelta(minutes=30)).isoformat(),
                  "options": options, "changes_made": False}
        return text, advice

    def candidates(self, cfg, data, now, monotonic):
        ts = now.timestamp()
        if cfg["enabled"] and cfg["disk_forecast"]:
            self._observe_disk(data.get("disk"), ts)
        if not cfg["enabled"] or quiet(cfg, now):
            self.reset_cpu()
            return []
        options = []
        if cfg["disk_forecast"]:
            notice = self._disk_notice(data.get("disk"))
            if notice:
                options.append(notice)
        battery = data.get("battery")
        if cfg["battery"] and battery is not None:
            percent, plugged = battery
            if not plugged and percent <= cfg["battery_percent"]:
                options.append(("battery", f"Pil yüzde {round(percent)} seviyesine düştü. Şarja bağlayabilirsin.", 3600))
        cpu = data.get("cpu")
        gap = monotonic - self.last_cpu_sample if self.last_cpu_sample is not None else None
        top_app = self._top_cpu_app(data.get("processes"), monotonic)
        if cfg["cpu"] and cpu is not None and cpu >= cfg["cpu_percent"]:
            if self.cpu_since is None or gap is None or gap > 90:
                self.cpu_since = monotonic
                self.high_samples = 0
                self.high_apps = []
            self.high_samples += 1
            self.high_apps.append(top_app)
            if monotonic - self.cpu_since >= cfg["cpu_seconds"]:
                text, advice = self._cpu_advice(cpu, now, monotonic)
                self.advice_by_key["cpu"] = advice
                options.append(("cpu", text, 3600))
        else:
            self.cpu_since = None
            self.high_samples = 0
            self.high_apps = []
            self.advice_by_key.pop("cpu", None)
        self.last_cpu_sample = monotonic
        if cfg["calendar"]:
            for item in data.get("events", []):
                delta = float(item.get("start_ts", 0)) - ts
                if not item.get("all_day") and 0 < delta <= cfg["event_minutes"] * 60:
                    title = str(item.get("title") or "Takvim etkinliği")[:180]
                    key = _key("event", [title, item["start_ts"], item.get("calendar")])
                    options.insert(0, (key, f"{max(1, math.ceil(delta / 60))} dakika sonra {title} etkinliğin var.", 172800))
        tomorrow = now.date() + dt.timedelta(days=1)
        if cfg["calendar_conflicts"]:
            conflicts = tomorrow_conflicts(data.get("tomorrow_events", []), tomorrow)
            if conflicts:
                clock, first, second = conflicts[0]
                extra = f" Ayrıca {len(conflicts) - 1} çakışma daha var." if len(conflicts) > 1 else ""
                options.insert(0, ("calendar_conflict:" + tomorrow.isoformat(),
                                   f"Yarınki takviminde saat {clock} civarında “{first}” ile “{second}” çakışıyor.{extra} "
                                   "Programı incelememi ister misin?", 172800))
        due = []
        if cfg["reminders"]:
            for item in data.get("reminders", []):
                stamp = float(item.get("due_ts", 0) or 0)
                if not item.get("completed") and stamp > 0 and dt.datetime.fromtimestamp(stamp).date() == tomorrow:
                    due.append(str(item.get("title") or "İsimsiz hatırlatıcı")[:180])
            if due:
                names = "; ".join(due[:3])
                suffix = " Başka hatırlatıcıların da var." if len(due) > 3 else ""
                options.append(("reminders:" + tomorrow.isoformat(), f"Yarın için hatırlatıcıların var: {names}.{suffix}", 172800))
        fresh = [(key, text) for key, text, ttl in options if ts - self.seen.get(key, 0) >= ttl]
        if ts - self.last_notice < cfg["interval_minutes"] * 60:
            # Genel bildirim aralığı yaklaşan etkinlik uyarılarını geciktirmesin (yoksa etkinlik
            # penceresi dolmadan hiç söylenmeyebiliyordu).
            return [(key, text) for key, text in fresh if key.startswith("event")]
        return fresh

    def delivered(self, key, now):
        ts = now.timestamp()
        self.seen = {k: v for k, v in self.seen.items() if ts - v < 3 * 86400}
        self.seen[key] = ts
        self.last_notice = ts
        self.latest_advice = self.advice_by_key.pop(key, None)
        self._save_state()


def get_proactive_advice(state_path=None) -> str:
    """Recall only the most recent delivered options; never execute an option."""
    path = Path(state_path) if state_path else Path(__file__).resolve().parents[1] / "config/proactive_state.json"
    try:
        advice = json.loads(path.read_text(encoding="utf-8")).get("latest_advice")
        if not isinstance(advice, dict) or advice.get("kind") != "sustained_cpu":
            raise ValueError("no advice")
        expires = dt.datetime.fromisoformat(advice["expires_at"])
        if dt.datetime.now(expires.tzinfo) > expires:
            raise ValueError("expired")
        return json.dumps({"status": "ok", "advice": advice}, ensure_ascii=False)
    except (OSError, ValueError, TypeError, KeyError):
        return json.dumps({"status": "unavailable", "detail": "Yakın zamanda verilmiş bir performans önerisi yok."}, ensure_ascii=False)


def open_settings(ui):
    import tkinter as tk
    from tkinter import messagebox
    from app_config import save_app_config
    old = getattr(ui, "_proactive_window", None)
    if old is not None and old.winfo_exists():
        old.lift()
        return
    window = tk.Toplevel(ui.root)
    ui._proactive_window = window
    window.title("JARVIS · Proaktif bildirimler")
    window.geometry("490x715")
    window.resizable(False, False)
    cfg = settings()
    variables = {}
    frame = tk.Frame(window, padx=18, pady=12)
    frame.pack(fill="both", expand=True)
    for key, label in (
        ("enabled", "Proaktif bildirimleri aç"),
        ("spoken", "Bildirimleri sesli oku (kapalıysa yalnızca günlükte göster)"),
        ("battery", "Düşük pil uyarısı"), ("calendar", "Yaklaşan takvim etkinlikleri / dersler"),
        ("cpu", "Uzun süren yüksek CPU kullanımı"), ("reminders", "Yarınki hatırlatıcılar"),
        ("disk_forecast", "Disk alanı azalma eğilimi / kritik seviye"),
        ("calendar_conflicts", "Yarınki takvim çakışmaları"),
        ("quiet_enabled", "Sessiz saatleri kullan (bu saatlerde bildirim gösterme)"),
    ):
        variable = tk.BooleanVar(value=cfg[key])
        variables[key] = variable
        tk.Checkbutton(frame, text=label, variable=variable).pack(anchor="w", pady=2)
    for key, label, low, high in (
        ("battery_percent", "Pil eşiği (%)", 5, 50), ("cpu_percent", "CPU eşiği (%)", 50, 100),
        ("cpu_seconds", "Yüksek CPU süresi (saniye)", 120, 1800),
        ("event_minutes", "Etkinlikten kaç dakika önce", 1, 60),
        ("interval_minutes", "Bildirimler arası en az dakika", 1, 180),
        ("quiet_start", "Sessiz saat başlangıcı (0–23)", 0, 23),
        ("quiet_end", "Sessiz saat bitişi (0–23)", 0, 23),
    ):
        row = tk.Frame(frame)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label).pack(side="left")
        variable = tk.StringVar(value=str(cfg[key]))
        variables[key] = variable
        tk.Spinbox(row, from_=low, to=high, textvariable=variable, width=6).pack(side="right")
    tk.Label(frame, text="Yarınki çakışmalar ve hatırlatıcılar günde bir kez; pil/CPU en sık saatte bir.\nDisk tahmini için en az 24 saatlik düzenli ölçüm gerekir.\nBildirimler JARVIS açıkken çalışır. Sesli okuma macOS Türkçe sesidir.",
             justify="left", wraplength=445).pack(anchor="w", pady=12)
    def save():
        try:
            values = {key: (var.get() if isinstance(DEFAULTS[key], bool) else int(var.get())) for key, var in variables.items()}
            normalized = settings(values)
            if values != normalized:
                raise ValueError()
            save_app_config({"proactive": normalized})
        except ValueError:
            messagebox.showerror("Geçersiz değer", "Sayısal alanları gösterilen aralıklarda doldur.", parent=window)
            return
        except OSError:
            messagebox.showerror("Kaydedilemedi", "Ayar dosyasına yazılamadı.", parent=window)
            return
        ui.write_log("SYS: Proaktif bildirim ayarları kaydedildi.")
        window.destroy()
    tk.Button(frame, text="Kaydet", command=save).pack(fill="x")
