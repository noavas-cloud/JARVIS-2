"""Modeless Call Agent setup and explicit, per-call review windows.

This module only manages the UI. Connection verification is read-only, and a
review decision is passed to the caller; this module never starts a phone call.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser


DASHBOARD_URL = "https://dashboard.vapi.ai"
CALLING_DOCS_URL = "https://docs.vapi.ai/phone-calling"
BACKGROUND = "#091b1f"
FIELD_BACKGROUND = "#0a1d23"
TEXT = "#c3f3ff"
ACCENT = "#73c1cc"
MUTED = "#819fa6"
WARNING = "#d6c052"
ERROR = "#ff6978"


def _window(ui, title, geometry):
    window = tk.Toplevel(ui.root)
    window.title(title)
    window.geometry(geometry)
    window.configure(bg=BACKGROUND)
    window.transient(ui.root)
    return window


class _ActionButton(tk.Label):
    """Paint locally: macOS native push-button surfaces ignore dark bg colors."""

    def __init__(self, parent, text, command, primary=False):
        self._command = command
        self._hover = self._pressed = False
        self._fill = "#174753" if primary else "#142d33"
        super().__init__(parent, text=text, bg=self._fill, fg="#ffffff",
            font=("Helvetica", 11, "bold"), padx=12, pady=9, relief="flat",
            borderwidth=0, highlightthickness=1, highlightbackground="#416b76",
            highlightcolor=ACCENT, disabledforeground="#9db0b5", cursor="hand2", takefocus=True)
        self.bind("<Enter>", lambda event: self._set_hover(True))
        self.bind("<Leave>", lambda event: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", self._keyboard)
        self.bind("<KeyRelease-space>", self._keyboard)

    def _paint(self):
        disabled = str(self.cget("state")) == "disabled"
        fill = "#1b2b2f" if disabled else "#2c6472" if self._pressed and self._hover else "#18444f" if self._hover else self._fill
        border = "#32494f" if disabled else "#68abbc" if self._hover else "#416b76"
        super().configure(bg=fill, highlightbackground=border, cursor="arrow" if disabled else "hand2")

    def configure(self, cnf=None, **kwargs):
        result = super().configure(cnf, **kwargs)
        if "state" in kwargs or isinstance(cnf, dict) and "state" in cnf:
            self._pressed = False
            self._paint()
        return result

    config = configure

    def _set_hover(self, value):
        self._hover = value
        self._paint()

    def _press(self, event):
        if str(self.cget("state")) != "disabled":
            self.focus_set()
            self._pressed = True
            self._paint()
        return "break"

    def _release(self, event):
        activate = self._pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._pressed = False
        self._paint()
        if activate:
            self.invoke()
        return "break"

    def _keyboard(self, event):
        self.invoke()
        return "break"

    def invoke(self):
        if str(self.cget("state")) != "disabled":
            return self._command()


def _button(parent, text, command, primary=False):
    return _ActionButton(parent, text, command, primary)


def _label(parent, text, **kwargs):
    options = {"bg": BACKGROUND, "fg": TEXT, "anchor": "w", "justify": "left"}
    options.update(kwargs)
    return tk.Label(parent, text=text, **options)


def _focus(window):
    window.deiconify()
    window.lift()
    window.focus_set()


def _safe_message(message, config):
    # Provider exceptions must not echo an entered secret back into the UI.
    result = str(message or "")
    secret = str(config.get("api_key", "") or "")
    if secret:
        result = result.replace(secret, "[gizli]")
    return result[:700]


def open_call_agent_settings(ui):
    """Open setup on the Tk thread; closing it never closes the main app."""
    from actions.call_agent import check_connection, save_settings, settings

    old = getattr(ui, "_call_agent_settings_window", None)
    if old is not None and old.winfo_exists():
        _focus(old)
        return old

    window = _window(ui, "JARVIS · Call Agent ayarları", "620x780")
    window.minsize(580, 760)
    ui._call_agent_settings_window = window
    frame = tk.Frame(window, bg=BACKGROUND, padx=20, pady=16)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    _label(frame, "CALL AGENT", fg=ACCENT, font=("Helvetica", 17, "bold")).grid(
        row=0, column=0, sticky="w")
    _label(frame, "Sağlayıcı: Vapi", fg=MUTED).grid(row=1, column=0, sticky="w", pady=(3, 8))
    _label(
        frame,
        "Önce Vapi hesabı, Türkçe konuşacak şekilde yapılandırılmış bir asistan, "
        "Vapi'ye aktarılmış ve dış arama yapabilen bir numara ile sağlayıcı kredisi "
        "hazırlayın. Hesap ve gerekli bilgiler olmadan arama yapılamaz. "
        "Aramalar sağlayıcı ücretlerine tabidir.",
        wraplength=560, fg=WARNING,
    ).grid(row=2, column=0, sticky="ew", pady=(0, 8))
    links = tk.Frame(frame, bg=BACKGROUND)
    links.grid(row=3, column=0, sticky="w", pady=(0, 8))
    _button(links, "Vapi panelini aç", lambda: webbrowser.open(DASHBOARD_URL)).pack(side="left", padx=(0, 8))
    _button(links, "Telefon arama rehberi", lambda: webbrowser.open(CALLING_DOCS_URL)).pack(side="left")

    try:
        config = settings()
        load_error = ""
    except (ValueError, OSError):
        config = {}
        load_error = "Kayıtlı ayarlar okunamadı. Bilgileri yeniden girerek kaydedebilirsiniz."
    enabled = tk.BooleanVar(window, value=bool(config.get("enabled", False)))
    tk.Checkbutton(
        frame, text="Telefon aramalarını etkinleştir", variable=enabled,
        bg=BACKGROUND, fg=TEXT, activebackground=BACKGROUND, activeforeground=TEXT,
        selectcolor=FIELD_BACKGROUND, anchor="w",
    ).grid(row=4, column=0, sticky="w", pady=(0, 4))

    fields = {}
    for row, (key, label, default) in enumerate((
        ("api_key", "Vapi Private API Key *", ""),
        ("phone_number_id", "Vapi Phone Number ID *", ""),
        ("assistant_id", "Vapi Assistant ID *", ""),
        ("caller_name", "Adınıza görüşülecek kişi (isteğe bağlı)", ""),
    ), start=5):
        group = tk.Frame(frame, bg=BACKGROUND)
        group.grid(row=row, column=0, sticky="ew", pady=4)
        _label(group, label).pack(anchor="w")
        variable = tk.StringVar(window, value=str(config.get(key, default) or ""))
        fields[key] = variable
        tk.Entry(
            group, textvariable=variable, show="*" if key == "api_key" else "",
            bg=FIELD_BACKGROUND, fg=TEXT, insertbackground=TEXT, relief="flat",
            highlightthickness=1, highlightbackground="#225562", highlightcolor=ACCENT,
        ).pack(fill="x", ipady=5, pady=(3, 0))

    duration_row = tk.Frame(frame, bg=BACKGROUND)
    duration_row.grid(row=9, column=0, sticky="ew", pady=(8, 3))
    _label(duration_row, "En uzun görüşme (saniye, 60–600)").pack(side="left")
    duration = tk.StringVar(window, value=str(config.get("max_duration_seconds", 300)))
    tk.Spinbox(
        duration_row, from_=60, to=600, increment=30, width=6, textvariable=duration,
        bg=FIELD_BACKGROUND, fg=TEXT, insertbackground=TEXT, buttonbackground="#152a2f",
    ).pack(side="right")
    _label(frame, "* Aramaları açmak ve bağlantıyı denetlemek için üç alan da gereklidir.",
           fg=MUTED, wraplength=560).grid(row=10, column=0, sticky="ew", pady=(3, 8))
    status = _label(frame, load_error or "Bağlantı denetimi telefon araması başlatmaz.",
                    fg=ERROR if load_error else MUTED, wraplength=560)
    status.grid(row=11, column=0, sticky="ew", pady=(0, 8))
    frame.rowconfigure(12, weight=1)
    buttons = tk.Frame(frame, bg=BACKGROUND)
    buttons.grid(row=13, column=0, sticky="ew")
    alive = True
    poll_id = None
    results = queue.Queue()

    def close():
        nonlocal alive, poll_id
        alive = False
        if poll_id is not None:
            window.after_cancel(poll_id)
            poll_id = None
        if getattr(ui, "_call_agent_settings_window", None) is window:
            ui._call_agent_settings_window = None
        window.destroy()

    def values(require_identifiers=False):
        result = {key: variable.get().strip() for key, variable in fields.items()}
        result["enabled"] = enabled.get()
        try:
            result["max_duration_seconds"] = int(duration.get().strip())
        except ValueError:
            raise ValueError("Görüşme süresi 60 ile 600 arasında tam sayı olmalı.") from None
        if not 60 <= result["max_duration_seconds"] <= 600:
            raise ValueError("Görüşme süresi 60 ile 600 saniye arasında olmalı.")
        if (require_identifiers or result["enabled"]) and any(
            not result[key] for key in ("api_key", "phone_number_id", "assistant_id")
        ):
            raise ValueError("Vapi API anahtarı, telefon numarası kimliği ve asistan kimliği gerekli.")
        return result

    def save():
        current = {}
        try:
            current = values()
            save_settings(current)
        except ValueError as exc:
            status.configure(text=_safe_message(exc, current), fg=ERROR)
            return
        except OSError:
            status.configure(text="Ayarlar kaydedilemedi. Dosya yazma iznini kontrol edin.", fg=ERROR)
            return
        status.configure(text="Call Agent ayarları kaydedildi. Her arama öncesinde ayrıntılar gösterilir.", fg=ACCENT)

    def poll_result():
        nonlocal poll_id
        poll_id = None
        if not alive:
            return
        try:
            result, current = results.get_nowait()
        except queue.Empty:
            poll_id = window.after(100, poll_result)
            return
        check_button.configure(state="normal")
        success = result.get("status") in ("ok", "ready", "connected", "success")
        message = result.get("message") or ("Bağlantı doğrulandı." if success else "Bağlantı doğrulanamadı.")
        status.configure(text=_safe_message(message, current), fg=ACCENT if success else ERROR)

    def check():
        try:
            current = values(require_identifiers=True)
        except ValueError as exc:
            status.configure(text=str(exc), fg=ERROR)
            return
        check_button.configure(state="disabled")
        status.configure(text="Vapi bağlantısı denetleniyor; telefon araması yapılmıyor…", fg=MUTED)

        def worker():
            try:
                result = check_connection(current)
                if not isinstance(result, dict):
                    raise ValueError()
            except Exception:
                result = {"status": "error", "message": "Bağlantı denetimi tamamlanamadı. Ağınızı ve Vapi bilgilerini kontrol edin."}
            results.put((result, current))

        threading.Thread(target=worker, name="JARVIS call setup check", daemon=True).start()
        poll_result()

    _button(buttons, "Kaydet", save, primary=True).pack(side="left")
    check_button = _button(buttons, "Bağlantıyı denetle", check)
    check_button.pack(side="left", padx=8)
    _button(buttons, "Kapat", close).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", close)
    _focus(window)
    return window


def show_call_review(ui, proposal, on_decision):
    """Display exact proposal fields; only the start button can return True."""
    window = _window(ui, "JARVIS · Telefon aramasını gözden geçir", "660x670")
    window.minsize(580, 570)
    frame = tk.Frame(window, bg=BACKGROUND, padx=20, pady=18)
    frame.pack(fill="both", expand=True)
    _label(frame, "ARAMA AYRINTILARI", fg=ACCENT, font=("Helvetica", 17, "bold")).pack(anchor="w", pady=(0, 10))
    _label(frame, "Başlatmadan önce alıcıyı ve görüşmenin amacını kontrol edin.",
           wraplength=610).pack(anchor="w", pady=(0, 10))
    details_frame = tk.Frame(frame, bg=BACKGROUND)
    details_frame.pack(fill="both", expand=True)
    scrollbar = tk.Scrollbar(details_frame)
    scrollbar.pack(side="right", fill="y")
    details = tk.Text(
        details_frame, bg=FIELD_BACKGROUND, fg=TEXT, relief="flat", wrap="word",
        padx=12, pady=10, height=14, yscrollcommand=scrollbar.set,
        highlightthickness=1, highlightbackground="#225562", takefocus=True,
    )
    scrollbar.configure(command=details.yview)
    details.pack(side="left", fill="both", expand=True)
    details.tag_configure("label", foreground=ACCENT, font=("Helvetica", 11, "bold"))
    for key, label in (
        ("id", "Arama kimliği"),
        ("business_name", "İşletme / alıcı"),
        ("phone_number", "Aranacak telefon numarası"),
        ("purpose", "Görüşmenin amacı"),
        ("source_url", "Numaranın kaynak bağlantısı"),
        ("max_duration_seconds", "En uzun görüşme (saniye)"),
        ("caller_name", "Adına görüşülecek kişi"),
    ):
        details.insert("end", label + "\n", "label")
        value = proposal.get(key)
        details.insert("end", (str(value) if value is not None and str(value) else "Belirtilmedi") + "\n\n")
    details.configure(state="disabled")
    _label(
        frame,
        "Arama Vapi üzerinden yapılır ve sağlayıcı ücretlerine tabidir. "
        "Asistan görüşmenin başında yapay zekâ olduğunu belirtir. "
        "Görüşme dökümü, sonuç özeti hazırlamak için kullanılır. "
        "JARVIS'i kapatmak görüşmeyi bitirmez; erken bitirmek için Vapi panelini kullanın.",
        fg=WARNING, wraplength=610,
    ).pack(fill="x", pady=(12, 6))
    _label(frame, "Rezervasyon isteniyorsa ayrıntıları amaçta açıkça belirtilmeli. Ödeme bilgisi paylaşılmaz.",
           fg=MUTED, wraplength=610).pack(fill="x", pady=(0, 12))
    buttons = tk.Frame(frame, bg=BACKGROUND)
    buttons.pack(fill="x")
    decided = False

    def decide(approved):
        nonlocal decided
        if decided:
            return
        decided = True
        window.destroy()
        on_decision(approved is True)

    _button(buttons, "ARAMAYI BAŞLAT", lambda: decide(True), primary=True).pack(side="left")
    _button(buttons, "VAZGEÇ", lambda: decide(False)).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", lambda: decide(False))
    window.bind("<Escape>", lambda event: decide(False))
    _focus(window)
    return window
