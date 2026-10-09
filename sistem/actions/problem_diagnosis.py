"""Bounded, read-only troubleshooting across network and common Mac problems."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import unicodedata

import psutil
import requests

from actions.performance_diagnosis import diagnose_slow_mac


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold().replace("ı", "i"))
    return "".join(char for char in value if not unicodedata.combining(char))


def _areas(query: str) -> list[str]:
    text = _fold(query)
    matches = []
    keywords = {
        "network": ("internet", "wi-fi", "wifi", "ag baglant", "baglanti", "dns", "modem", "router", "ping", "paket kayb"),
        "storage": ("disk", "depolama", "bos alan", "yer kalmad", "yer yok", "dolu"),
        # Metin katlanmış olduğundan "ı" burada hiç eşleşmezdi; tek başına "kapanıyor" uygulama sorunudur.
        "power": ("pil", "batarya", "sarj", "uyku", "uyumuyor", "guc", "kendi kendine kapan",
                  "aniden kapan", "mac kapan", "bilgisayar kapan"),
        "audio": ("mikrofon", "hoparlor", "kulaklik", "ses gelm", "ses gitm", "duymuyor", "ses cikm"),
    }
    for area, words in keywords.items():
        if any(word in text for word in words):
            matches.append(area)
    if not matches and any(word in text for word in ("uygulama", "acilmiyor", "acilmadi", "cokuyor", "coktu", "crash", "kapaniyor", "kapandi")):
        matches.append("application")
    if any(word in text for word in ("yavas", "kasiyor", "donuyor", "takiliyor", "performans", "cpu", "islemci", "ram", "bellek", "isiniyor", "fan")):
        if not matches or "application" in matches or any(word in text for word in ("bilgisayar", "mac", "cpu", "islemci", "ram", "bellek", "fan")):
            matches.append("performance")
    return matches or ["network", "performance", "storage", "power", "audio"]


def _run(command: list[str], timeout: float = 4) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def _link() -> dict:
    ok, raw = _run(["/sbin/route", "-n", "get", "default"], timeout=3)
    interface = re.search(r"^\s*interface:\s*(\S+)", raw, re.M)
    gateway = re.search(r"^\s*gateway:\s*(\S+)", raw, re.M)
    if not ok:
        if not raw:
            return {"status": "unavailable", "default_route": None,
                    "interface": None, "gateway": None}
        return {"status": "ok", "default_route": False, "interface": None, "gateway": None}
    name = interface.group(1) if interface else None
    gateway_ip = gateway.group(1) if gateway else None
    try:
        ipaddress.ip_address(gateway_ip)
    except (ValueError, TypeError):
        gateway_ip = None
    up = None
    has_ipv4 = None
    if name:
        try:
            stat = psutil.net_if_stats().get(name)
            up = bool(stat.isup) if stat else None
            addrs = psutil.net_if_addrs().get(name, [])
            has_ipv4 = any(item.family == socket.AF_INET for item in addrs)
        except (OSError, psutil.Error):
            pass
    wifi_device = None
    wifi_power = None
    hardware_ok, hardware = _run(["/usr/sbin/networksetup", "-listallhardwareports"], timeout=3)
    if hardware_ok:
        found = re.search(r"Hardware Port: Wi-Fi\s+Device: (\S+)", hardware)
        wifi_device = found.group(1) if found else None
    if wifi_device:
        power_ok, power_text = _run(["/usr/sbin/networksetup", "-getairportpower", wifi_device], timeout=3)
        if power_ok:
            wifi_power = " on" in power_text.lower()
    return {"status": "ok", "default_route": bool(name), "interface": name,
            "gateway": gateway_ip, "interface_up": up, "has_ipv4": has_ipv4,
            "wifi_device": wifi_device, "wifi_power": wifi_power}


def _ping(target: str) -> dict:
    try:
        ipaddress.ip_address(target)
    except ValueError:
        return {"status": "unavailable", "detail": "Ping hedefi geçersiz."}
    _, output = _run(["/sbin/ping", "-n", "-c", "4", "-W", "1000", target], timeout=7)
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
    counts = re.search(r"(\d+) packets transmitted, (\d+) packets received", output)
    rtt = re.search(r"(?:round-trip|rtt) min/avg/max/\S+\s*=\s*[\d.]+/([\d.]+)", output)
    if not loss or not counts:
        return {"status": "unavailable", "detail": "ICMP testi tamamlanamadı."}
    return {"status": "ok", "sent": int(counts.group(1)), "received": int(counts.group(2)),
            "loss_percent": float(loss.group(1)),
            "average_ms": float(rtt.group(1)) if rtt else None,
            "note": "ICMP yanıtı verilmemesi tek başına bağlantı kesintisi kanıtı değildir."}


def _dns_one(domain: str) -> dict:
    started = time.monotonic()
    ok, output = _run(["/usr/bin/dscacheutil", "-q", "host", "-a", "name", domain], timeout=4)
    return {"domain": domain, "resolved": bool(ok and re.search(r"^(?:ip_address|ipv6_address):\s*\S+", output, re.M)),
            "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _tcp_one(address: str) -> dict:
    started = time.monotonic()
    try:
        with socket.create_connection((address, 443), timeout=2):
            return {"address": address, "connected": True,
                    "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except OSError:
        return {"address": address, "connected": False,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _https_one(url: str) -> dict:
    started = time.monotonic()
    try:
        with requests.get(url, timeout=(2, 3), allow_redirects=True, stream=True) as response:
            return {"service": url.split("/")[2], "reachable": response.status_code < 500,
                    "http_status": response.status_code,
                    "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except requests.RequestException:
        return {"service": url.split("/")[2], "reachable": False, "http_status": None,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _network_conclusion(checks: dict) -> tuple[str, str, str]:
    link = checks["link"]
    dns = checks["dns"]
    tcp = checks["public_tcp"]
    https = checks["https"]
    router = checks["router_ping"]
    public_ping = checks["public_ping"]
    tcp_ok = any(item["connected"] for item in tcp)
    https_ok = any(item["reachable"] for item in https)
    dns_ok = [item for item in dns if item["resolved"]]
    if link["status"] != "ok":
        return "inconclusive", "low", "Yerel ağ yolu okunamadı; bağlantı nedeni doğrulanamadı."
    if not link["default_route"]:
        if tcp_ok or https_ok:
            return "route_inconclusive", "low", "IPv4 varsayılan yolu görünmüyor, fakat dış bağlantı çalışıyor; ağ yolu ölçümü tek başına arıza göstermiyor."
        return "no_default_route", "high", "Varsayılan ağ yolu bulunamadı; sorun yerel ağ bağlantısında görünüyor."
    if link.get("interface_up") is False or link.get("has_ipv4") is False:
        if tcp_ok or https_ok:
            return "interface_inconclusive", "low", "Seçilen ağ arayüzünde adres veya bağlantı eksik görünüyor, fakat dış bağlantı çalışıyor; başka arayüz kullanılıyor olabilir."
        return "local_link", "high", "Etkin ağ arayüzü bağlı görünmüyor veya IPv4 adresi yok."
    if tcp_ok and not dns_ok:
        return "dns_failure", "high", "Doğrudan IP bağlantısı çalışıyor fakat test edilen adlar çözümlenemedi; DNS sorunu olası."
    dns_times = sorted(item["elapsed_ms"] for item in dns_ok)
    fast_tcp = min((item["elapsed_ms"] for item in tcp if item["connected"]), default=None)
    if len(dns_times) >= 2 and dns_times[len(dns_times)//2] >= 800 and fast_tcp is not None and fast_tcp < 300:
        median = dns_times[len(dns_times)//2]
        return "dns_slow", "moderate", f"DNS çözümlemesi bu ölçümde yavaş: ortanca {median} milisaniye; doğrudan IP bağlantısı {fast_tcp} milisaniye."
    if not tcp_ok and not https_ok and router["status"] == "ok" and router["received"] == 0:
        return "local_or_upstream", "moderate", "Ağ geçidi ping yanıtı vermedi ve dış bağlantı kurulamadı; yerel ağ veya sağlayıcı tarafı incelenmeli. Yönlendirici ICMP'yi engelliyor olabilir."
    if tcp_ok and dns_ok and not https_ok:
        return "https_failure", "moderate", "IP ve DNS çalışıyor, fakat iki HTTPS servisine erişilemedi; TLS, proxy veya servis tarafı incelenmeli."
    if router["status"] == "ok" and 0 < router["loss_percent"] < 100:
        return "gateway_icmp_loss", "moderate", f"Kısa ağ geçidi ping testinde yüzde {router['loss_percent']:g} yanıt kaybı görüldü; yönlendirici ICMP'yi sınırlıyor da olabilir."
    if public_ping["status"] == "ok" and 0 < public_ping["loss_percent"] < 100:
        return "icmp_loss", "moderate", f"Kısa dış ping testinde yüzde {public_ping['loss_percent']:g} paket kaybı görüldü; daha uzun ölçümle doğrulanmalı."
    if tcp_ok and https_ok and dns_ok:
        if len(https) > 1 and not all(item["reachable"] for item in https):
            return "one_service_failed", "low", "Genel bağlantı çalışıyor; test edilen dış servislerden biri yanıt vermedi."
        return "not_reproduced", "low", "Şu anki ağ, DNS ve dış servis testlerinde belirgin bir sorun yeniden üretilemedi. Aralıklı sorun için tekrar ölçmek gerekir."
    return "inconclusive", "low", "Bazı ağ kontrolleri tamamlanamadı; eldeki verilerle tek bir neden doğrulanamadı."


def _network() -> dict:
    link = _link()
    with ThreadPoolExecutor(max_workers=9) as pool:
        jobs = {
            "router": pool.submit(_ping, link["gateway"]) if link.get("gateway") else None,
            "public_ping": pool.submit(_ping, "1.1.1.1"),
            "dns": [pool.submit(_dns_one, domain) for domain in ("example.com", "apple.com", "cloudflare.com")],
            "tcp": [pool.submit(_tcp_one, ip) for ip in ("1.1.1.1", "8.8.8.8")],
            "https": [pool.submit(_https_one, url) for url in ("https://www.apple.com", "https://example.com")],
        }
        checks = {"link": link,
                  "router_ping": jobs["router"].result() if jobs["router"] else {"status": "unavailable", "detail": "Geçit adresi yok."},
                  "public_ping": jobs["public_ping"].result(),
                  "dns": [job.result() for job in jobs["dns"]],
                  "public_tcp": [job.result() for job in jobs["tcp"]],
                  "https": [job.result() for job in jobs["https"]]}
    code, confidence, message = _network_conclusion(checks)
    return {"status": "partial" if code == "inconclusive" else "ok",
            "checks": checks, "diagnosis": {"code": code, "confidence": confidence,
                                            "message": message},
            "spoken_summary": message, "changes_made": False}


def _storage() -> dict:
    try:
        disk = psutil.disk_usage("/")
        free_gib = round(disk.free / 1024**3, 1)
        free_percent = round(100 * disk.free / max(1, disk.total), 1)
        low = free_percent < 10 or free_gib < 10
        message = (f"Diskte {free_gib} gigabayt boş alan kaldı; alan azlığı bir soruna katkıda bulunabilir."
                   if low else f"Diskte {free_gib} gigabayt boş alan var; alan azlığı şu an görünmüyor.")
        return {"status": "ok", "checks": {"free_gib": free_gib, "free_percent": free_percent},
                "diagnosis": {"code": "low_space" if low else "not_reproduced",
                              "confidence": "high" if low else "low", "message": message},
                "spoken_summary": message, "changes_made": False}
    except (OSError, psutil.Error):
        return {"status": "unavailable", "spoken_summary": "Depolama ölçümü alınamadı."}


def _power(query: str = "") -> dict:
    sleep_issue = any(word in _fold(query) for word in ("uyku", "uyumuyor"))
    sleep_checks = None
    if sleep_issue:
        ok, output = _run(["/usr/bin/pmset", "-g", "assertions"], timeout=4)
        if ok:
            flags = {}
            for key in ("PreventSystemSleep", "PreventUserIdleSystemSleep", "PreventUserIdleDisplaySleep"):
                match = re.search(r"^\s*" + key + r"\s+(\d+)", output, re.M)
                flags[key] = int(match.group(1)) if match else None
            sleep_checks = flags
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            if sleep_checks and any(value == 1 for value in sleep_checks.values()):
                message = "macOS'ta uykuyu engelleyen etkin bir güç isteği görünüyor; hangi uygulamanın sorumlu olduğu ayrıca incelenmeli."
                return {"status": "partial", "checks": {"sleep_assertions": sleep_checks},
                        "diagnosis": {"code": "sleep_assertion", "confidence": "moderate", "message": message},
                        "spoken_summary": message, "changes_made": False}
            return {"status": "unavailable", "spoken_summary": "Pil verisi alınamadı."}
        checks = {"percent": round(battery.percent), "plugged": bool(battery.power_plugged),
                  "estimated_minutes_left": (math.ceil(battery.secsleft / 60)
                                             if not battery.power_plugged and battery.secsleft > 0 else None)}
        low = checks["percent"] <= 15 and not checks["plugged"]
        checks["sleep_assertions"] = sleep_checks
        blocking_sleep = bool(sleep_issue and sleep_checks and any(value == 1 for value in sleep_checks.values()))
        message = ("macOS'ta uykuyu engelleyen etkin bir güç isteği görünüyor; hangi uygulamanın sorumlu olduğu ayrıca incelenmeli."
                   if blocking_sleep else f"Pil yüzde {checks['percent']} ve şarja bağlı değil; düşük pil doğrulandı."
                   if low else f"Pil yüzde {checks['percent']}. Tek ölçüm, hızlı tükenmenin nedenini göstermiyor.")
        return {"status": "ok", "checks": checks,
                "diagnosis": {"code": "sleep_assertion" if blocking_sleep else "low_battery" if low else "inconclusive",
                              "confidence": "moderate" if blocking_sleep else "high" if low else "low", "message": message},
                "spoken_summary": message, "changes_made": False}
    except (OSError, psutil.Error, ValueError):
        return {"status": "unavailable", "spoken_summary": "Pil verisi alınamadı."}


def _audio(query: str = "") -> dict:
    checks = {"input_device": None, "output_device": None, "output_muted": None, "output_volume": None}
    try:
        import pyaudio
        audio = pyaudio.PyAudio()
        try:
            try:
                checks["input_device"] = audio.get_default_input_device_info().get("name")
            except (OSError, IOError):
                pass
            try:
                checks["output_device"] = audio.get_default_output_device_info().get("name")
            except (OSError, IOError):
                pass
        finally:
            audio.terminate()
    except (ImportError, OSError):
        pass
    ok, output = _run(["/usr/bin/osascript", "-e", "get volume settings"], timeout=3)
    if ok:
        mute = re.search(r"output muted:(true|false)", output)
        volume = re.search(r"output volume:(\d+)", output)
        checks["output_muted"] = mute.group(1) == "true" if mute else None
        checks["output_volume"] = int(volume.group(1)) if volume else None
    input_issue = any(word in _fold(query) for word in ("mikrofon", "duymuyor", "ses gitm"))
    if input_issue and checks["input_device"] is None:
        code, confidence, message = "no_input_device", "moderate", "Varsayılan mikrofon aygıtı bulunamadı; mikrofon bağlantısı veya seçimi kontrol edilmeli."
    elif checks["output_muted"] is True and not input_issue:
        code, confidence, message = "muted", "high", "Mac çıkış sesi kapalı görünüyor."
    elif checks["output_volume"] == 0 and not input_issue:
        code, confidence, message = "volume_zero", "high", "Mac çıkış sesi sıfır seviyesinde görünüyor."
    elif checks["input_device"] is None and not input_issue:
        code, confidence, message = "no_input_device", "moderate", "Varsayılan mikrofon aygıtı bulunamadı; mikrofon bağlantısı veya seçimi kontrol edilmeli."
    elif checks["output_device"] is None:
        code, confidence, message = "no_output_device", "moderate", "Varsayılan ses çıkış aygıtı bulunamadı."
    else:
        code, confidence, message = "inconclusive", "low", "Giriş ve çıkış aygıtları görünüyor; bu kontrol gerçek konuşma algısını veya ses kalitesini doğrulamaz."
    return {"status": "ok", "checks": checks,
            "diagnosis": {"code": code, "confidence": confidence, "message": message},
            "spoken_summary": message, "changes_made": False}


def _performance() -> dict:
    result = json.loads(diagnose_slow_mac())
    return {"status": result["status"], "checks": result["sections"],
            "diagnosis": {"code": result["possible_causes"][0] if result["possible_causes"] else "inconclusive",
                          "confidence": "moderate" if result["possible_causes"] else "low",
                          "message": result["spoken_summary"]},
            "options": result.get("options", []),
            "spoken_summary": result["spoken_summary"], "changes_made": False}


def _installed_apps() -> list[Path]:
    candidates = []
    for folder in (Path("/Applications"), Path("/System/Applications"),
                   Path("/System/Applications/Utilities"), Path.home() / "Applications"):
        try:
            candidates.extend(path for path in folder.glob("*.app") if path.is_dir())
        except OSError:
            continue
    return candidates


def _application(query: str) -> dict:
    text = _fold(query)
    candidates = _installed_apps()
    matches = [path for path in candidates if re.search(
        r"(?<!\w)" + re.escape(_fold(path.stem)) + r"(?!\w)", text)]
    if not matches:
        message = "Hangi uygulamanın sorun çıkardığını belirleyemedim; uygulama adını söylersen çalışmasını ve çökme kayıtlarını kontrol edebilirim."
        return {"status": "partial", "diagnosis": {"code": "app_unknown", "confidence": "low", "message": message},
                "spoken_summary": message, "changes_made": False}
    path = max(matches, key=lambda item: len(item.stem))
    name = path.stem
    running = False
    try:
        for process in psutil.process_iter(["name"]):
            try:
                if _fold(process.info.get("name") or "") == _fold(name):
                    running = True
                    break
            except (psutil.Error, OSError):
                continue
    except (psutil.Error, OSError):
        pass
    crash_count = 0
    reports = Path.home() / "Library/Logs/DiagnosticReports"
    try:
        cutoff = time.time() - 24 * 3600
        with os.scandir(reports) as entries:
            for entry in entries:
                if (entry.is_file(follow_symlinks=False)
                        and entry.name.lower().endswith((".ips", ".crash"))
                        # Yeni macOS: "Safari-2026-09-29-101010.ips", eski: "Safari_2019-…crash"
                        and _fold(entry.name).startswith((_fold(name) + "_", _fold(name) + "-"))
                        and entry.stat(follow_symlinks=False).st_mtime >= cutoff):
                    crash_count += 1
    except OSError:
        pass
    if crash_count:
        code, confidence = "recent_crash_report", "moderate"
        message = f"{name} için son 24 saatte {crash_count} çökme raporu bulundu. Bu, çökme olduğunu destekler; nedeni için rapor içeriği ayrıca incelenmeli."
    elif not running:
        code, confidence = "installed_not_running", "low"
        message = f"{name} kurulu, fakat şu anda çalışmıyor. Bu kontrol neden açılmadığını doğrulamadı."
    else:
        code, confidence = "running_no_cause", "low"
        message = f"{name} şu anda çalışıyor; bu kontrolde sorunun nedeni doğrulanmadı."
    return {"status": "ok", "checks": {"app": name, "installed_path": str(path),
                                       "running": running, "recent_crash_reports": crash_count},
            "diagnosis": {"code": code, "confidence": confidence, "message": message},
            "spoken_summary": message, "changes_made": False}


COLLECTORS = {"network": _network, "performance": _performance,
              "storage": _storage, "power": _power, "audio": _audio,
              "application": _application}


def diagnose_problem(query: str) -> str:
    """Choose relevant independent checks, then report only supported conclusions."""
    selected = _areas(query)
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = {area: pool.submit(COLLECTORS[area], query) if area in ("audio", "power", "application")
                   else pool.submit(COLLECTORS[area]) for area in selected}
        sections = {}
        for area, future in futures.items():
            try:
                sections[area] = future.result()
            except Exception:
                sections[area] = {"status": "unavailable",
                                  "spoken_summary": f"{area} kontrolü tamamlanamadı."}
    summary = " ".join(sections[area]["spoken_summary"] for area in selected)
    unavailable = [area for area, result in sections.items() if result["status"] == "unavailable"]
    partial = any(result["status"] == "partial" for result in sections.values())
    return json.dumps({"status": "error" if len(unavailable) == len(selected) else "partial" if unavailable or partial else "ok",
                       "selected_areas": selected, "sections": sections,
                       "unavailable_areas": unavailable, "spoken_summary": summary,
                       "measured_at": datetime.now().astimezone().isoformat(),
                       "changes_made": False}, ensure_ascii=False)
