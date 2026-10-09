"""One-shot, persistent watches for downloads, battery, app CPU and websites."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import psutil
import requests

from actions.performance_diagnosis import _app_name


STATE_PATH = Path(__file__).resolve().parents[1] / "config" / "follow_watches.json"
DOWNLOADS = Path.home() / "Downloads"
TEMP_SUFFIXES = (".crdownload", ".download", ".part", ".partial")
MAX_WATCHES = 12
CPU_NORMAL_SECONDS = 30
SITE_RECHECK_SECONDS = 30
TRANSIENT_FIELDS = {"last_check", "last_file", "stable_since", "successes", "low_since"}
SENSITIVE_QUERY_KEYS = {"token", "access_token", "refresh_token", "auth", "authorization",
                        "password", "secret", "api_key", "apikey", "code", "session"}


def _reply(status: str, **fields) -> str:
    return json.dumps({"status": status, **fields}, ensure_ascii=False)


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value).casefold().replace("ı", "i"))
    return "".join(char for char in text if not unicodedata.combining(char))


def _clean_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("İzlenecek sitenin adresi gerekli.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Yalnızca açık http/https site adresi izlenebilir.")
    if any(key.casefold() in SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
        raise ValueError("Kimlik bilgisi içeren bağlantı takip için kaydedilemez; temiz site adresini söyle.")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _site_available(url: str) -> bool:
    try:
        with requests.get(url, headers={"User-Agent": "JARVIS site availability watch"},
                          timeout=(2, 3), allow_redirects=True, stream=True) as response:
            return 200 <= response.status_code < 400
    except requests.RequestException:
        return False


def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _active_downloads(directory: Path) -> list[Path]:
    return [path for path in directory.iterdir()
            if path.name.lower().endswith(TEMP_SUFFIXES) and not path.is_symlink()]


def _download_target(raw: str, directory: Path) -> tuple[Path | None, str | None]:
    value = str(raw or "").strip()
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            if path.name != value or value in (".", ".."):
                raise ValueError("Dosya için tam yol veya yalnızca İndirilenler'deki dosya adını söyle.")
            path = directory / value
        if path.name.lower().endswith(TEMP_SUFFIXES):
            suffix = next(s for s in TEMP_SUFFIXES if path.name.lower().endswith(s))
            return path.with_name(path.name[:-len(suffix)]), str(path)
        return path, None
    partials = _active_downloads(directory)
    if len(partials) != 1:
        return None, None
    partial = partials[0]
    suffix = next(s for s in TEMP_SUFFIXES if partial.name.lower().endswith(s))
    final_name = partial.name[:-len(suffix)]
    if not final_name or final_name.lower().startswith("unconfirmed "):
        return None, None
    return partial.with_name(final_name), str(partial)


def _process_totals(app_name: str) -> dict[str, float]:
    totals = {}
    wanted = _fold(app_name)
    for process in psutil.process_iter():
        try:
            if _fold(_app_name(process)) != wanted:
                continue
            times = process.cpu_times()
            totals[f"{process.pid}:{process.create_time()}"] = times.user + times.system
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
    return totals


def _matching_apps(query: str) -> list[str]:
    names = set()
    wanted = _fold(query).strip()
    for process in psutil.process_iter():
        try:
            name = _app_name(process)
            folded = _fold(name)
            if folded == wanted or (wanted and wanted in folded):
                names.add(name)
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
    exact = [name for name in names if _fold(name) == wanted]
    return sorted(exact or names)


def _cpu_usage(before: dict[str, float], after: dict[str, float], seconds: float) -> float:
    return 100 * sum(max(0.0, total - before[key]) for key, total in after.items()
                     if key in before) / max(0.001, seconds)


class WatchManager:
    def __init__(self, state_path: Path = STATE_PATH, downloads: Path = DOWNLOADS):
        self.state_path = Path(state_path)
        self.downloads = Path(downloads)
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        self._runtime: dict[str, dict] = {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            for item in data.get("watches", []):
                if (isinstance(item, dict) and item.get("id")
                        and item.get("kind") in ("download", "battery", "cpu", "site")
                        and item.get("status") in ("watching", "ready")):
                    self._items[str(item["id"])] = {
                        key: value for key, value in item.items() if key not in TRANSIENT_FIELDS
                    }
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "watches": [
            {key: value for key, value in item.items() if key not in TRANSIENT_FIELDS}
            for item in self._items.values()
        ]}, ensure_ascii=False)
        fd, temporary = tempfile.mkstemp(prefix=".follow-watches-", dir=self.state_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def start(self, kind: str, target: str = "", threshold_percent: float = 0,
              direction: str = "above", normal_percent: float = 20) -> str:
        kind = str(kind or "").strip().lower()
        if kind not in ("download", "battery", "cpu", "site"):
            return _reply("error", message="Takip türü download, battery, cpu veya site olmalı.")
        with self._lock:
            if len(self._items) >= MAX_WATCHES:
                return _reply("error", message="En fazla 12 eşzamanlı takip olabilir; önce birini iptal et.")
        item = {"id": uuid.uuid4().hex[:8], "kind": kind, "status": "watching",
                "created_at": time.time(), "last_check": 0.0}
        try:
            if kind == "download":
                if not target and not self.downloads.is_dir():
                    return _reply("error", message="İndirilenler klasörüne erişilemiyor.")
                path, partial = _download_target(target, self.downloads)
                if path is None:
                    return _reply("needs_target", message=(
                        "Tek bir etkin indirme belirlenemedi. Dosyanın adını veya tam yolunu söyle; "
                        "birden fazla indirme varsa hangisini izleyeceğimi belirt."))
                if not path.parent.is_dir():
                    return _reply("error", message="Dosyanın indirileceği klasöre erişilemiyor.")
                final = _file_state(path)
                # Firefox, indirme sürerken son adla 0 baytlık yer tutucu oluşturur: tamamlanmış sayılmaz.
                if partial and final is not None and final[0] > 0:
                    return _reply("needs_target", message=(
                        "Aynı adlı tamamlanmış bir dosya zaten var; yeni indirmenin kesin adını belirt."))
                if final is not None and not partial:
                    return _reply("already_met", message=f"{path.name} zaten indirilmiş görünüyor.")
                item.update(target=str(path), partial=partial, last_file=None, stable_since=None)
                label = path.name
            elif kind == "battery":
                threshold = float(threshold_percent)
                if not 1 <= threshold <= 100:
                    return _reply("error", message="Pil eşiği yüzde 1 ile 100 arasında olmalı.")
                direction = str(direction or "above").strip().lower()
                if direction not in ("above", "below"):
                    return _reply("error", message="Pil yönü above veya below olmalı.")
                battery = psutil.sensors_battery()
                if battery is None:
                    return _reply("error", message="Mac pil verisi okunamadı.")
                met = battery.percent >= threshold if direction == "above" else battery.percent <= threshold
                if met:
                    return _reply("already_met", message=f"Pil zaten yüzde {round(battery.percent)} seviyesinde.")
                item.update(target=threshold, direction=direction)
                label = f"Pil %{threshold:g} {'seviyesine yükselince' if direction == 'above' else 'seviyesine düşünce'}"
            elif kind == "cpu":
                query = str(target or "").strip()
                if len(query) < 2:
                    return _reply("needs_target", message="Hangi uygulamanın CPU kullanımını izleyeyim?")
                threshold = float(normal_percent)
                if not 1 <= threshold <= 100:
                    return _reply("error", message="Normal CPU eşiği yüzde 1 ile 100 arasında olmalı.")
                matches = _matching_apps(query)
                if len(matches) != 1:
                    return _reply("needs_target", message="Tek uygulama belirlenemedi.", candidates=matches[:8])
                app = matches[0]
                before = _process_totals(app)
                start = time.monotonic()
                time.sleep(0.35)
                after = _process_totals(app)
                if not after:
                    return _reply("error", message=f"{app} artık çalışmıyor.")
                usage = _cpu_usage(before, after, time.monotonic() - start)
                if usage <= threshold:
                    return _reply("already_met", message=f"{app} CPU kullanımı zaten normal: yaklaşık %{usage:.0f}.")
                item.update(target=app, normal_percent=threshold, low_since=None)
                self._runtime[item["id"]] = {"cpu_totals": after, "cpu_at": time.monotonic()}
                label = f"{app} CPU kullanımı %{threshold:g} altına 30 saniye boyunca düşünce"
            else:
                url = _clean_url(target)
                if _site_available(url):
                    return _reply("already_met", message=f"{url} şu anda erişilebilir.")
                item.update(target=url, successes=0)
                label = f"{url} yeniden erişilebilir olunca"
        except (ValueError, OSError, TypeError) as exc:
            return _reply("error", message=str(exc))

        item["label"] = label
        with self._lock:
            identity = ("kind", "target", "direction", "normal_percent")
            for existing in self._items.values():
                if all(existing.get(key) == item.get(key) for key in identity):
                    return _reply("already_watching", id=existing["id"],
                                  message=f"Bu koşul zaten takip ediliyor: {existing['label']}.")
            if len(self._items) >= MAX_WATCHES:
                return _reply("error", message="Takip sınırına ulaşıldı.")
            self._items[item["id"]] = item
            try:
                self._save()
            except OSError:
                self._items.pop(item["id"], None)
                self._runtime.pop(item["id"], None)
                return _reply("error", message="Takip kaydedilemedi.")
        return _reply("watching", id=item["id"], kind=kind, target=item["target"],
                      message=f"Takip başlatıldı: {label}. Koşul gerçekleşince bir kez haber vereceğim.")

    def list(self) -> str:
        with self._lock:
            items = [{key: item.get(key) for key in ("id", "kind", "label", "status")}
                     for item in self._items.values()]
        return _reply("ok", watches=items, count=len(items))

    def cancel(self, watch_id: str) -> str:
        watch_id = str(watch_id or "").strip()
        with self._lock:
            if watch_id not in self._items:
                return _reply("error", message="Bu kimlikte etkin takip bulunamadı.")
            item = self._items.pop(watch_id)
            self._runtime.pop(watch_id, None)
            try:
                self._save()
            except OSError:
                self._items[watch_id] = item
                return _reply("error", message="Takip iptali kaydedilemedi.")
        return _reply("cancelled", id=watch_id, message=f"Takip iptal edildi: {item['label']}.")

    def _check(self, item: dict, now: float) -> dict:
        kind = item["kind"]
        if kind == "battery":
            battery = psutil.sensors_battery()
            if battery is not None:
                threshold = float(item["target"])
                met = battery.percent >= threshold if item["direction"] == "above" else battery.percent <= threshold
                if met:
                    item["message"] = f"Takip ettiğim pil seviyesi yüzde {round(battery.percent)} oldu."
                    item["status"] = "ready"
        elif kind == "download":
            path = Path(item["target"])
            partial = item.get("partial")
            if partial and Path(partial).exists():
                item["stable_since"] = None
                item["last_file"] = None
            else:
                state = _file_state(path)
                if state is None:
                    item["stable_since"] = None
                    item["last_file"] = None
                elif list(state) == item.get("last_file"):
                    if item.get("stable_since") is not None and now - item["stable_since"] >= 5:
                        item["message"] = f"{path.name} dosyasının indirilmesi tamamlandı."
                        item["status"] = "ready"
                else:
                    item["last_file"] = list(state)
                    item["stable_since"] = now
        elif kind == "site":
            if _site_available(item["target"]):
                item["successes"] = int(item.get("successes", 0)) + 1
                if item["successes"] >= 2:
                    host = urlsplit(item["target"]).hostname or item["target"]
                    item["message"] = f"Takip ettiğim {host} sitesi yeniden erişilebilir."
                    item["status"] = "ready"
            else:
                item["successes"] = 0
        elif kind == "cpu":
            app = item["target"]
            current = _process_totals(app)
            previous = self._runtime.get(item["id"])
            moment = time.monotonic()
            self._runtime[item["id"]] = {"cpu_totals": current, "cpu_at": moment}
            if current and previous and previous["cpu_totals"]:
                usage = _cpu_usage(previous["cpu_totals"], current, moment - previous["cpu_at"])
                if usage <= float(item["normal_percent"]):
                    if item.get("low_since") is None:
                        item["low_since"] = now
                    elif now - item["low_since"] >= CPU_NORMAL_SECONDS:
                        item["message"] = (f"{app} uygulamasının CPU kullanımı normale döndü; "
                                           f"son ölçüm yaklaşık yüzde {round(usage)}.")
                        item["status"] = "ready"
                else:
                    item["low_since"] = None
            else:
                item["low_since"] = None
        item["last_check"] = now
        if item["status"] == "ready":
            item["ready_at"] = now
        return item

    def check_once(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self._lock:
            ids = list(self._items)
        for watch_id in ids:
            with self._lock:
                original = self._items.get(watch_id)
                if original is None or original["status"] != "watching":
                    continue
                interval = SITE_RECHECK_SECONDS if original["kind"] == "site" else 10
                if now - float(original.get("last_check", 0)) < interval:
                    continue
                item = dict(original)
            checked = self._check(item, now)
            with self._lock:
                if watch_id in self._items and self._items[watch_id]["status"] == "watching":
                    self._items[watch_id] = checked
                    if checked["status"] == "ready":
                        self._save()
        with self._lock:
            return [dict(item) for item in self._items.values() if item["status"] == "ready"]

    def delivered(self, watch_id: str) -> None:
        with self._lock:
            if self._items.get(watch_id, {}).get("status") == "ready":
                item = self._items.pop(watch_id)
                self._runtime.pop(watch_id, None)
                try:
                    self._save()
                except OSError:
                    self._items[watch_id] = item
                    raise
