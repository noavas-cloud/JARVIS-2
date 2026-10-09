#!/usr/bin/env python3
"""
JARVIS — Telefon köprüsü (uygulama içi)
────────────────────────────────────────
JARVIS arayüzündeki "JARVIS TELEFON" panelinin arka ucu. Telefon web
sunucusunu (jarvis_web/server.py), Mac ajanını (agent.py) ve Cloudflare
tünelini alt-süreç olarak başlatır; genel adres + token'ı verir; durdurur.

UI'dan bağımsız — tkinter'e dokunmaz. Durum callback'lerle bildirilir.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import tempfile
import urllib.request
import urllib.error
from urllib.parse import urlencode
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR  = BASE_DIR / "jarvis_web"

# Cloudflare'ın hata satırlarındaki API adresi (https://api.trycloudflare.com) tünel adresi değildir.
_URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")


TAILSCALE_PATHS = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                   "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale")


def _tailscale_exe() -> str | None:
    hit = _find_exe("tailscale")
    if hit:
        return hit
    return next((p for p in TAILSCALE_PATHS if Path(p).is_file()), None)


def tailscale_address(exe: str, run=subprocess.run) -> tuple[str | None, str]:
    """Tailscale açık ve girişliyse bu Mac'in sabit adresi (https://mac-adi.xxx.ts.net), değilse (None, neden)."""
    try:
        r = run([exe, "status", "--json"], capture_output=True, text=True, timeout=8)
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, "Tailscale durumu okunamadı."
    if data.get("BackendState") != "Running":
        return None, "Tailscale açık değil ya da hesaba giriş yapılmamış."
    name = str((data.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net", name):
        return None, "Tailscale'de MagicDNS kapalı (Tailscale yönetim sayfası › DNS › MagicDNS'i aç)."
    return "https://" + name, ""


def _find_exe(name: str) -> str | None:
    """GUI uygulamalarında PATH eksik olabilir — bilinen yerlere de bak."""
    import shutil
    hit = shutil.which(name)
    if hit:
        return hit
    for p in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(p).is_file():
            return p
    return None


class PhoneBridge:
    def __init__(self):
        self._procs: list[subprocess.Popen] = []
        self._tunnel_log: Path | None = None
        self.url: str | None = None
        self.token: str | None = None
        # "tailscale" = sabit adres (telefon bir kez eşleşir); "cloudflare" = her başlatmada değişen geçici adres.
        self.mode: str | None = None
        self.notice = ""
        self._tailscale: str | None = None
        self.running = False
        self._starting = False
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()

    # ── Yardımcılar ──────────────────────────────────────────────────────────
    def _python(self) -> str:
        """Sunucu/ajanı çalıştıracak Python — bu uygulamanın yorumlayıcısı."""
        return sys.executable or "python3"

    def _read_token(self) -> str | None:
        try:
            cfg = json.loads((WEB_DIR / "web_config.json").read_text(encoding="utf-8"))
            return str(cfg.get("token", "") or "") or None
        except Exception:
            return None

    def cloudflared_available(self) -> bool:
        return _find_exe("cloudflared") is not None

    def _spawn(self, *args, **kwargs):
        # stop() and startup may race while a network readiness check is pending.
        # Register each child under the same lock used by process cleanup.
        with self._state_lock:
            if self._cancelled.is_set():
                raise RuntimeError("Başlatma iptal edildi.")
            process = subprocess.Popen(*args, **kwargs)
            self._procs.append(process)
            return process

    # ── Başlat ───────────────────────────────────────────────────────────────
    def start(self, on_ready, on_error, on_status=None):
        """Süreçleri başlatır; hazır olunca on_ready(url, token) çağrılır.
        Hepsi ayrı bir thread'de — UI donmaz."""
        with self._state_lock:
            if self.running or self._starting:
                return
            self._starting = True
            self._cancelled.clear()
        threading.Thread(
            target=self._start_worker,
            args=(on_ready, on_error, on_status or (lambda s: None)),
            daemon=True,
        ).start()

    def _start_worker(self, on_ready, on_error, on_status):
        try:
            if not WEB_DIR.exists():
                on_error("Telefon dosyaları bulunamadı (jarvis_web).")
                return

            cf = _find_exe("cloudflared")
            ts = _tailscale_exe()
            if not cf and not ts:
                on_error("cloudflared kurulu değil. Terminal'de: brew install cloudflared")
                return

            py = self._python()
            env_note = {"PYTHONUNBUFFERED": "1"}
            import os
            env = {**os.environ, **env_note}

            on_status("Sunucu başlatılıyor...")
            server = self._spawn(
                [py, "-u", "server.py", "--host", "127.0.0.1", "--no-ssl"], cwd=str(WEB_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            # Listen only on loopback; the tunnel supplies authenticated TLS.
            # Wait for our actual server, not a fixed delay or an unrelated listener.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self._cancelled.wait(0.15):
                    return
                if server.poll() is not None:
                    raise RuntimeError("Telefon sunucusu açılmadı. 8765 portunda başka bir JARVIS oturumu olabilir.")
                self.token = self._read_token()
                try:
                    self._admin_request("/api/android/admin/status")
                    break
                except (OSError, ValueError):
                    continue
            else:
                raise RuntimeError("Telefon sunucusu hazır olmadı.")

            on_status("Mac ajanı başlatılıyor...")
            self._spawn(
                [py, "-u", "agent.py"], cwd=str(WEB_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

            url = None
            self.notice = "" if ts else "Tailscale kurulu değil."
            if ts:
                on_status("Sabit adres (Tailscale) hazırlanıyor...")
                url, self.notice = self._start_tailscale(ts)
            if url:
                self.mode = "tailscale"
            elif not cf:
                raise RuntimeError(self.notice + " (Geçici adres için: brew install cloudflared)")
            else:
                self.mode = "cloudflare"
                on_status("Genel adres alınıyor...")
                fd, log_path = tempfile.mkstemp(prefix="jarvis_phone_", suffix=".log")
                os.close(fd)
                self._tunnel_log = Path(log_path)
                with open(self._tunnel_log, "w") as logf:
                    self._spawn(
                        [cf, "tunnel", "--url", "http://localhost:8765"],
                        stdout=logf, stderr=subprocess.STDOUT)

            # URL'i bekle (en çok ~40 sn)
            for _ in range(0 if url else 80):
                if self._cancelled.is_set():
                    return
                if any(p.poll() is not None for p in self._procs):
                    raise RuntimeError("Telefon bağlantısı başlatılırken bir bileşen durdu.")
                try:
                    txt = self._tunnel_log.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    txt = ""
                m = _URL_RE.search(txt)
                if m:
                    url = m.group(0)
                    break
                self._cancelled.wait(0.5)

            self.token = self._read_token()
            if not url:
                raise RuntimeError("Güvenli telefon adresi alınamadı. İnternet bağlantısını kontrol edip tekrar dene.")
            for _ in range(40):
                if self._cancelled.wait(0.25):
                    return
                if self._admin_request("/api/android/admin/status").get("agent_connected"):
                    break
            else:
                raise RuntimeError("Mac ajanı bağlanamadı; telefon araçları henüz hazır değil.")
            self.url = url
            self.running = True
            full = f"{url}/?t={self.token}" if self.token else url
            on_ready(full, self.token or "")
        except Exception as exc:
            cancelled = self._cancelled.is_set()
            self.stop()
            if not cancelled:
                on_error(f"Başlatılamadı: {exc}")
        finally:
            self._starting = False
            if self._cancelled.is_set():
                self._stop_processes()

    def _start_tailscale(self, exe: str) -> tuple[str | None, str]:
        """Sunucuyu yalnız bu kişinin Tailscale cihazlarına, hep aynı https adresinden açar."""
        url, why = tailscale_address(exe)
        if not url:
            return None, why
        try:
            r = subprocess.run([exe, "serve", "--bg", "--https=443", "http://127.0.0.1:8765"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None, "Tailscale adresi açılamadı (zaman aşımı)."
        if r.returncode != 0:
            detail = " ".join((r.stderr or r.stdout or "").split())[:300]
            return None, "Tailscale adresi açılamadı: " + (detail or "bilinmeyen hata")
        self._tailscale = exe
        print(f"[Telefon] Sabit adres (Tailscale): {url}", flush=True)
        return url, ""

    def _admin_request(self, path: str, method: str = "GET") -> dict:
        token = self.token or self._read_token()
        if not token:
            raise ValueError("Telefon sunucusu erişim anahtarı hazır değil.")
        req = urllib.request.Request(
            "http://127.0.0.1:8765" + path, method=method,
            data=b"{}" if method == "POST" else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            # Local administration must never go through a configured HTTP proxy.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as response:
                return json.loads(response.read(16384))
        except urllib.error.HTTPError as exc:
            raise OSError("Telefon sunucusu isteği reddetti (" + str(exc.code) + ").") from None

    def create_android_pairing(self) -> dict:
        if not self.running or not self.url or not self.url.startswith("https://"):
            raise ValueError("Önce JARVIS TELEFON bağlantısını başlat.")
        result = self._admin_request("/api/android/admin/pairing", "POST")
        result["url"] = self.url
        result["qr_uri"] = "jarvis://pair?" + urlencode({"url": self.url, "code": result["code"]})
        result["download_url"] = self.url + "/android/download"
        return result

    def revoke_android_devices(self) -> dict:
        if not self.running:
            raise ValueError("Önce telefon bağlantısını başlat.")
        return self._admin_request("/api/android/admin/revoke-all", "POST")

    # ── Durdur ───────────────────────────────────────────────────────────────
    def stop(self):
        self._cancelled.set()
        self.running = False
        self.url = None
        self._stop_processes()
        exe, self._tailscale = self._tailscale, None
        if exe:
            # JARVIS TELEFON kapalıyken Tailscale adresi de kapansın (adın kendisi değişmez).
            try:
                subprocess.run([exe, "serve", "--https=443", "off"], capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _stop_processes(self):
        with self._state_lock:
            procs, self._procs = self._procs, []
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        # Kısa süre sonra hâlâ yaşayan varsa zorla kapat
        time.sleep(0.3)
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
            try:
                p.wait(timeout=2)
            except Exception:
                pass
        if self._tunnel_log:
            try:
                self._tunnel_log.unlink(missing_ok=True)
            except OSError:
                pass
