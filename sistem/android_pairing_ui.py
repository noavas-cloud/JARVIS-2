"""Android companion setup, separate from the existing browser phone panel."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk


class AndroidPairingWindow:
    def __init__(self, root, bridge):
        self.bridge = bridge
        self.window = tk.Toplevel(root)
        self.window.title("JARVIS • Android eşleştirme")
        self.window.geometry("440x780")
        self.window.minsize(400, 760)
        self.window.configure(bg="#0c1d21")
        self.window.transient(root)
        self.data = None
        self.photo = None
        self.busy = False
        self.expires_at = 0
        self.mode = "pair"
        self._label("J A R V I S   /   A N D R O I D", 17, "#85e7ff").pack(pady=(22, 6))
        self._label("Bilgisayarındaki JARVIS, telefonunda.", 11).pack()
        self.instructions = self._label(
            "Android uygulamasında ‘Bilgisayarla eşleştir’\n→ ‘QR kodu tara’ düğmesine dokun.", 11)
        self.instructions.pack(pady=(18, 8))
        self.qr = self._label("Eşleştirme kodu hazırlanıyor…", 11)
        self.qr.pack(pady=6)
        self.code = self._label("", 24, "#85e7ff")
        self.code.pack(pady=4)
        self.status = self._label("", 10, "#98b1b7")
        self.status.pack(pady=(0, 8))
        self.address = tk.Entry(self.window, readonlybackground="#16272b", fg="#d8e7eb",
            relief="flat", font=("Helvetica", 10), justify="center")
        self.address.pack(fill="x", padx=24, ipady=8)
        self.address.insert(0, bridge.url or "")
        self.address.configure(state="readonly")
        self._button("Uygulamayı indir • APK QR kodu", self.show_download).pack(fill="x", padx=24, pady=(14, 6))
        self._button("Yeni eşleştirme kodu oluştur", self.refresh).pack(fill="x", padx=24, pady=6)
        self._button("Eşleşmiş telefonların erişimini kaldır", self.revoke, danger=True).pack(fill="x", padx=24, pady=6)
        self.note = self._label("", 9, "#83979c")
        self.note.pack(pady=(10, 8))
        self._update_note()
        self.refresh()
        self.tick()

    def _update_note(self):
        if getattr(self.bridge, "mode", None) == "tailscale":
            text = ("Sabit adres (Tailscale): telefon bir kez eşleşir, adres değişmez.\n"
                    "Telefonda da Tailscale açık olmalı. Mac ve JARVIS TELEFON açık kalmalı.")
        else:
            why = getattr(self.bridge, "notice", "")
            text = ("Mac ve JARVIS TELEFON açık kalmalı. İnternet gerekir.\n"
                    "Geçici adres: JARVIS TELEFON yeniden başlatılırsa adres değişir."
                    + (f"\nSabit adres kullanılamadı: {why}" if why else ""))
        self.note.configure(text=text)

    def _label(self, text, size, color="#d8e7eb"):
        return tk.Label(self.window, text=text, bg="#0c1d21", fg=color,
                        font=("Helvetica", size), justify="center", wraplength=395)

    def _button(self, text, action, danger=False):
        button = tk.Label(self.window, text=text, bg="#162d33" if not danger else "#252014",
            fg="#85e7ff" if not danger else "#cfb15f", font=("Helvetica", 11, "bold"),
            padx=10, pady=11, cursor="hand2")
        button.bind("<Button-1>", lambda _event: action())
        return button

    def _alive(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _work(self, call, done):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text="Hazırlanıyor…")
        def worker():
            try:
                result, error = call(), None
            except Exception as exc:
                result, error = None, str(exc)
            def finish():
                self.busy = False
                if not self._alive():
                    return
                if error:
                    self.status.configure(text=error, fg="#cfb15f")
                else:
                    done(result)
            try:
                self.window.after(0, finish)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _qr(self, value):
        import qrcode
        qr = qrcode.QRCode(box_size=6, border=4)
        qr.add_data(value)
        qr.make(fit=True)
        pic = qr.make_image(fill_color="#0a1e23", back_color="#edfbff").convert("RGB")
        self.photo = ImageTk.PhotoImage(pic.resize((240, 240), Image.Resampling.NEAREST))
        self.qr.configure(image=self.photo, text="")

    def refresh(self):
        self._update_note()
        def done(data):
            self.data = data
            self._set_address(data["url"])
            self.expires_at = time.monotonic() + data.get("expires_in", 300)
            self.mode = "pair"
            self.instructions.configure(text="Android uygulamasında ‘Bilgisayarla eşleştir’\n→ ‘QR kodu tara’ düğmesine dokun.")
            self.code.configure(text=data["code"][:4] + "  " + data["code"][4:])
            self.status.configure(fg="#98b1b7")
            self._qr(data["qr_uri"])
        self._work(self.bridge.create_android_pairing, done)

    def _set_address(self, url):
        self.address.configure(state="normal")
        self.address.delete(0, "end")
        self.address.insert(0, url)
        self.address.configure(state="readonly")

    def show_download(self):
        if not self.bridge.running or not self.bridge.url:
            self.status.configure(text="Önce JARVIS TELEFON bağlantısını başlat.")
            return
        self.mode = "download"
        self._set_address(self.bridge.url)
        self.instructions.configure(text="Telefonun Kamera uygulamasıyla okut.\nAPK dosyasını indirip kur; sonra eşleştirme kodu oluştur.")
        self._qr(self.bridge.url + "/android/download")
        self.code.configure(text="JARVIS Android")
        self.status.configure(text="Android 8 ve üzeri • APK kurulumu", fg="#98b1b7")

    def revoke(self):
        if self.busy or not messagebox.askyesno("Telefon erişimleri",
            "Eşleşmiş tüm Android telefonların erişimi kaldırılsın mı?\nTekrar bağlanmak için yeni eşleştirme gerekir.", parent=self.window):
            return
        def done(_result):
            self.expires_at = 0
            self.mode = "revoked"
            self.qr.configure(image="", text="Telefon erişimleri kaldırıldı.")
            self.code.configure(text="")
            self.status.configure(text="Yeniden bağlamak için yeni kod oluştur.", fg="#98b1b7")
        self._work(self.bridge.revoke_android_devices, done)

    def tick(self):
        if not self._alive():
            return
        if self.mode == "pair" and self.expires_at and not self.busy:
            left = max(0, int(self.expires_at - time.monotonic()))
            self.status.configure(text=(f"Kod tek kullanımlık • Kalan süre {left // 60}:{left % 60:02d}"
                                      if left else "Kodun süresi doldu. Yeni eşleştirme kodu oluştur."))
        self.window.after(1000, self.tick)
