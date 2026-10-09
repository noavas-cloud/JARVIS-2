"""Local owner pairing and Telegram settings; never starts a conversation itself."""
import queue
import threading
import tkinter as tk
import time
import webbrowser

from call_agent_ui import _ActionButton, BACKGROUND, FIELD_BACKGROUND, TEXT, ACCENT, MUTED


def open_telegram_settings(ui):
    from actions.telegram_bridge import settings, save_settings, begin_pairing, disconnect_owner, check_connection

    old = getattr(ui, "_telegram_settings_window", None)
    if old is not None and old.winfo_exists():
        old.deiconify()
        old.lift()
        return old
    window = tk.Toplevel(ui.root)
    ui._telegram_settings_window = window
    window.title("JARVIS · Telegram")
    window.geometry("640x660")
    window.minsize(590, 620)
    window.configure(bg=BACKGROUND)
    window.transient(ui.root)
    frame = tk.Frame(window, bg=BACKGROUND, padx=22, pady=18)
    frame.pack(fill="both", expand=True)

    def label(text, **kwargs):
        options = dict(bg=BACKGROUND, fg=TEXT, anchor="w", justify="left", wraplength=570)
        options.update(kwargs)
        widget = tk.Label(frame, text=text, **options)
        widget.pack(fill="x", pady=(0, 10))
        return widget

    label("TELEGRAM", fg=ACCENT, font=("Helvetica", 18, "bold"))
    label("Mac'te JARVIS açıkken telefonundan mesaj veya sesli not gönder. "
          "Yalnızca burada eşleştirdiğin özel Telegram hesabı kabul edilir.")
    label("1. Telegram'da resmî @BotFather hesabından /newbot ile bot oluştur.\n"
          "2. Verilen token'ı aşağıya kaydet ve bağlantıyı aç.\n"
          "3. Eşleştirme kodunu oluştur; kendi botuna özel mesaj olarak gönder.", fg=MUTED)
    cfg = settings()
    token = tk.StringVar(value=cfg["token"])
    enabled = tk.BooleanVar(value=cfg["enabled"])
    label("Bot token (yalnızca bu Mac'te saklanır)")
    tk.Entry(frame, textvariable=token, show="•", bg=FIELD_BACKGROUND, fg=TEXT,
             insertbackground=ACCENT, relief="flat", font=("Helvetica", 12)).pack(fill="x", ipady=9, pady=(0, 10))
    status = tk.StringVar(value=cfg["last_status"])
    pair_text = tk.StringVar(value="Henüz eşleştirme kodu oluşturulmadı.")
    owner_text = tk.StringVar()
    code = {"value": "", "expires": 0}
    pending = queue.Queue()

    def save():
        try:
            updated = save_settings({"token": token.get(), "enabled": enabled.get()})
            status.set("Ayarlar kaydedildi. " + ("Telegram açık." if updated["enabled"] else "Telegram kapalı."))
            return True
        except Exception:
            status.set("Kaydedilemedi. Token biçimini ve ayar dosyası erişimini kontrol et.")
            enabled.set(settings()["enabled"])
            return False

    tk.Checkbutton(frame, text="Telegram bağlantısı açık", variable=enabled, command=save,
                   bg=BACKGROUND, fg=TEXT, activebackground=BACKGROUND, activeforeground=TEXT,
                   selectcolor=FIELD_BACKGROUND, font=("Helvetica", 12)).pack(anchor="w", pady=(0, 10))

    def test():
        entered = token.get().strip()
        test_button.configure(state="disabled")
        status.set("Bot bağlantısı kontrol ediliyor…")

        def worker():
            try:
                result = check_connection(token=entered)
            except Exception:
                result = {"message": "Bot bağlantısı kontrol edilemedi."}
            pending.put(result)
        threading.Thread(target=worker, daemon=True).start()

    buttons = tk.Frame(frame, bg=BACKGROUND)
    buttons.pack(fill="x", pady=(0, 14))
    _ActionButton(buttons, "Kaydet", save, True).pack(side="left", padx=(0, 8))
    test_button = _ActionButton(buttons, "Botu kontrol et", test)
    test_button.pack(side="left", padx=(0, 8))
    _ActionButton(buttons, "BotFather", lambda: webbrowser.open("https://t.me/BotFather")).pack(side="left")

    def pair():
        if not save():
            return
        try:
            code["value"] = begin_pairing()
            code["expires"] = time.time() + 300
            pair_text.set("Botuna gönder:  /start " + code["value"])
            status.set("Kod 5 dakika geçerli. Yalnızca kendi botuna özel mesaj olarak gönder.")
        except Exception:
            status.set("Kod oluşturulamadı. Token'ı kaydet, bağlantıyı aç; zaten eşleşmişse önce bağlantıyı kaldır.")

    def disconnect():
        try:
            disconnect_owner()
            enabled.set(False)
            code["value"] = ""
            status.set("Hesap bağlantısı kaldırıldı; Telegram kapalı.")
        except Exception:
            status.set("Hesap bağlantısı kaldırılamadı.")

    label("", textvariable=owner_text, fg=MUTED)
    row = tk.Frame(frame, bg=BACKGROUND)
    row.pack(fill="x", pady=(0, 12))
    _ActionButton(row, "Eşleştirme kodu oluştur", pair, True).pack(side="left", padx=(0, 8))
    _ActionButton(row, "Hesap bağlantısını kaldır", disconnect).pack(side="left")
    tk.Entry(frame, textvariable=pair_text, state="readonly", readonlybackground=FIELD_BACKGROUND,
             fg=ACCENT, relief="flat", font=("Menlo", 11)).pack(fill="x", ipady=9, pady=(0, 12))
    label("", textvariable=status, fg=ACCENT)
    label("Sesli not sınırı: 60 saniye / 10 MB. Grup mesajları işlenmez. "
          "Sesli notlar cevap için JARVIS'in bağlı AI hizmetine iletilir. "
          "Kesilmiş işlemler kendiliğinden yeniden çalıştırılmaz.", fg=MUTED)
    _ActionButton(frame, "Kapat", window.destroy).pack(anchor="e", side="bottom")

    def refresh():
        if not window.winfo_exists():
            return
        current = settings()
        owner_text.set("Hesap: eşleştirildi" if current["owner_id"] else "Hesap: eşleştirilmedi")
        if code["value"] and (time.time() >= code["expires"] or not current["pair_hash"]):
            code["value"] = ""
            pair_text.set("Eşleştirme tamamlandı." if current["owner_id"] else "Kodun süresi doldu veya iptal edildi.")
        if not code["value"] and pair_text.get().startswith("Botuna"):
            pair_text.set("Eşleştirme kodu iptal edildi.")
        try:
            result = pending.get_nowait()
            status.set(result["message"])
            test_button.configure(state="normal")
        except queue.Empty:
            pass
        window.after(700, refresh)

    refresh()
    return window
