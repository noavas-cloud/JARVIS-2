"""JARVIS TELEFON: telefon köprüsünü (phone_bridge → jarvis_web/server.py + Mac ajanı + Tailscale/Cloudflare) yönetir.

Arayüzden bağımsızdır; durumunu kendi alanlarında tutar, arayüz her karede okur. Sunucu ve ajan JARVIS 2'nin araç
kaydını kullanır (jarvis_web/agent.py). Asıl JARVIS'le aynı 8765 portunu kullanır (telefon aynı adrese bağlanır,
yeniden eşleşmez) → ikisinin köprüsü aynı anda açılamaz; açıksa sunucu açılmaz ve neden yazılır.
"""

from __future__ import annotations

import json
import threading
import time

from jarvis.paths import BASE_DIR, ensure_import_path

ensure_import_path()

DEVICES_FILE = BASE_DIR / "jarvis_web" / "android_devices.json"


class PhoneHost:
    def __init__(self, state):
        self.state = state
        self._bridge = None
        self.busy = False
        self.status = "kapalı"
        self.url = ""            # web bağlantısı (tarayıcı) adresi, token'lı
        self.token = ""
        self.mode = ""           # tailscale | cloudflare
        self.notice = ""
        self.devices: list[str] = []
        self.connected = 0
        self._poll_stop = threading.Event()
        self.refresh_devices()

    @property
    def bridge(self):
        if self._bridge is None:
            from phone_bridge import PhoneBridge
            self._bridge = PhoneBridge()
        return self._bridge

    @property
    def running(self) -> bool:
        return bool(self._bridge is not None and self._bridge.running)

    # ── Başlat / durdur ─────────────────────────────────────────────────────────────────────
    def toggle(self):
        if self.busy:
            return
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self, auto: bool = False):
        if self.busy or self.running:
            return
        self.busy = True
        self.status = "başlatılıyor…"
        if auto:
            self.state.log("sys", "JARVIS Telefon kendiliğinden başlatılıyor (sabit adres, Tailscale).")

        def ready(url, token):
            self.busy = False
            self.url, self.token = url, token
            b = self._bridge
            self.mode, self.notice = getattr(b, "mode", "") or "", getattr(b, "notice", "") or ""
            self.status = "açık · sabit adres (Tailscale)" if self.mode == "tailscale" else "açık · geçici adres"
            self.state.log("sys", "JARVIS Telefon açık." + ("" if self.mode == "tailscale" else
                           " Geçici adres kullanılıyor; telefonu yeniden eşleştirmen gerekebilir."))
            self._start_polling()

        def error(msg):
            self.busy = False
            self.status = "hata"
            self.state.log("err", "JARVIS Telefon: " + str(msg))

        def status(text):
            self.status = text

        self.bridge.start(on_ready=ready, on_error=error, on_status=status)

    def stop(self):
        if self._bridge is None:
            return
        self.busy = True
        self.status = "durduruluyor…"

        def run():
            try:
                self._bridge.stop()
            finally:
                self._poll_stop.set()
                self.busy = False
                self.url = self.token = ""
                self.connected = 0
                self.status = "kapalı"
                self.refresh_devices()
                self.state.log("sys", "JARVIS Telefon durduruldu.")
        threading.Thread(target=run, daemon=True).start()

    def shutdown(self):
        """Uygulama kapanırken (sunucu, ajan, tünel ve Tailscale adresi kapanır)."""
        self._poll_stop.set()
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass

    def auto_start(self):
        """Tailscale açık ve girişliyse (sabit adres) açılışta başlat. Tailscale yoksa başlatma: geçici Cloudflare
        adresi her açılışta internete açılmasın. Ayar: phone_autostart (varsayılan açık)."""
        from app_config import load_app_config
        if not load_app_config().get("phone_autostart", True):
            return

        def check():
            from jarvis.app import original_running
            if original_running():
                print("[TELEFON] Asıl JARVIS açık; telefon bağlantısı kendiliğinden başlatılmadı.", flush=True)
                return
            try:
                from phone_bridge import _tailscale_exe, tailscale_address
                exe = _tailscale_exe()
                ok = bool(exe and tailscale_address(exe)[0])
            except Exception:
                ok = False
            if ok and not self.running and not self.busy:
                self.start(auto=True)
        threading.Thread(target=check, daemon=True).start()

    # ── Eşleşmiş / bağlı telefonlar ─────────────────────────────────────────────────────────
    def refresh_devices(self):
        def work():
            names = []
            try:
                data = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))   # yalnız okunur
                names = list(dict.fromkeys(
                    str(d.get("name", "")).strip()[:40] for d in (data.get("devices") or {}).values()
                    if isinstance(d, dict) and str(d.get("name", "")).strip()))
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            self.devices = names
            if self.running:
                try:
                    st = self._bridge._admin_request("/api/android/admin/status")
                    self.connected = int(st.get("phones_connected", 0))
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _start_polling(self):
        self._poll_stop = threading.Event()
        stop = self._poll_stop

        def loop():
            while not stop.wait(5.0):
                if not self.running:
                    break
                self.refresh_devices()
        self.refresh_devices()
        threading.Thread(target=loop, daemon=True).start()

    def summary(self) -> tuple[str, str]:
        """(ana satır, alt satır) — arayüzdeki TELEFON kartı için."""
        if self.busy:
            return "BAĞLANIYOR…" if not self.running else "KAPANIYOR…", self.status
        if self.running:
            if self.connected:
                return f"{self.connected} TELEFON BAĞLI", ", ".join(self.devices[:2])
            return "AÇIK · BEKLENİYOR", (", ".join(self.devices[:2]) or "eşleşmiş telefon yok")
        return "KAPALI", (("Eşleşmiş: " + ", ".join(self.devices[:2])) if self.devices else "eşleşmiş telefon yok")

    def pairing_window(self, root):
        if not self.running:
            self.state.log("sys", "Önce JARVIS Telefon'u başlat, sonra Android'i eşleştir.")
            return None
        from android_pairing_ui import AndroidPairingWindow
        return AndroidPairingWindow(root, self._bridge)

    # ── Ara ve söyle ────────────────────────────────────────────────────────────────────────
    async def call_and_say(self, args: dict, confirm) -> dict:
        """Birini arayıp mesajı okur: önce bağlı telefondan (onay telefonda), yoksa Mac'ten (onay pencerede).
        confirm(title, text) → await edilebilir, True/False döner."""
        import asyncio
        from actions import call_relay
        message = call_relay.clean_message(args.get("message", ""))
        if not message:
            return {"status": "error", "message": "Söylenecek mesaj boş ya da çok uzun (en çok 600 karakter)."}
        name = str(args.get("recipient_name", "") or "").strip()[:80]
        raw = str(args.get("phone_number", "") or "").strip()
        number = call_relay.normalize_number(raw) if raw else ""
        if raw and not number:
            return {"status": "invalid_number", "message": "Telefon numarası geçersiz; ülke koduyla ya da 0 ile başlayarak söyle."}
        if not name and not number:
            return {"status": "needs_recipient", "message": "Kimin aranacağını (ad ya da numara) söyle."}
        if await asyncio.to_thread(call_relay.phone_connected):
            if not number and name:
                found = await asyncio.to_thread(call_relay.resolve_contact, name, "")
                number = found.get("number", "") if found.get("status") == "ok" else ""
            self.state.log("sys", "Arama isteği telefona gönderildi; telefondaki onay ekranında ARA'ya bas.")
            return await asyncio.to_thread(call_relay.call_via_phone, name, number, message)
        found = await asyncio.to_thread(call_relay.resolve_contact, name, number)
        if found.get("status") != "ok":
            return {"status": found.get("status", "not_found"), "via": "mac", "candidates": found.get("candidates", []),
                    "message": ("Telefonda JARVIS açık ve bağlı değil; Mac'te de bu kişinin numarası bulunamadı. "
                                "Numarayı söyle ya da telefondaki JARVIS'i açıp Mac'e bağla.")}
        number = found["number"]
        who = found.get("name") or name or number
        ok = await confirm("Mac'ten ara ve söyle",
                           f"Telefon bağlı değil. Mac'in Telefon uygulamasıyla aranacak:\n\n{who}  ({number})\n\n"
                           f"Okunacak mesaj:\n“{message}”\n\nBu Mac'e bağlı iPhone yoksa yalnızca FaceTime sesli "
                           "arama yapılabilir. Mesaj Mac hoparlöründen okunur. Aransın mı?")
        if not ok:
            return {"status": "cancelled", "via": "mac", "message": "Arama onaylanmadı; yapılmadı."}
        return {"_mac_call": (number, message)}
