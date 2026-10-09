"""JARVIS 2 ana penceresi (yeni HUD). Küre ui/orb.py'de (asıl JARVIS'le birebir aynı).

Düzen:  üst şerit (marka · duyulan cümle · bağlantı · ayarlar · kapat)
        sol sütun  SELAM kartı · SİSTEM göstergeleri · ETKİNLİK (JARVIS'in son işleri)
        orta       küre + altında simgeli kontrol düğmeleri
        sağ sütun  SOHBET (balonlar + yazma kutusu) — ayarlar açılınca yerini ayarlar paneli alır
        alt şerit  mikrofon düzeyi ve durumu
Çizim tek tuvalde (Canvas) her karede yeniden yapılır (~30 kare/sn); tıklama alanları her karede kaydedilir.
"""

from __future__ import annotations

import io
import math
import os
import pwd
import re
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import psutil
from PIL import Image, ImageTk

from jarvis.paths import APP_NAME, ICON_DIR, LOG_DIR, ensure_import_path
from jarvis.ui import theme as T
from jarvis.ui.orb import OrbView, face_size, image_size
from jarvis.ui.sound import SoundManager

ensure_import_path()
from app_config import has_gemini_api_key, load_app_config, save_app_config  # noqa: E402

TOP_H, FOOT_H, DOCK_H = 64, 30, 80
GAP = 14
# Ekran izleme düğmesi yok (kullanıcı isteği 03.10): sesle "ekranımı izle" ya da F8; açıkken üstte kırmızı uyarı görünür.
DOCK = (("mic", "MİKROFON", "F4"), ("pause", "DURAKLAT", "F5"), ("cam", "KAMERA", "F6"),
        ("brain", "BEYİN", "⌘B"), ("power", "KAPAT", "⌘Q"))
VOICES = ["Charon", "Puck", "Aoede", "Kore", "Fenrir", "Leda", "Orus", "Zephyr"]
LOG_MAX_LINES = 1500


def _first_name() -> str:
    try:
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        return full.split()[0] if full else ""
    except Exception:
        return ""


def _greeting() -> str:
    h = time.localtime().tm_hour
    return "Günaydın" if 5 <= h < 12 else "İyi günler" if h < 17 else "İyi akşamlar" if h < 22 else "İyi geceler"


def _ago(ts: float) -> str:
    d = max(0, int(time.time() - ts))
    return "şimdi" if d < 45 else f"{d // 60} dk" if d < 3600 else f"{d // 3600} sa"


class JarvisWindow:
    def __init__(self, state, core):
        self.s = state
        self.core = core
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self._set_icon()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.W, self.H = min(sw - 48, 1540), min(sh - 130, 940)
        self._window_geometry = f"{self.W}x{self.H}+{(sw - self.W) // 2}+{max(32, (sh - self.H) // 2 - 30)}"
        self._normal_size = (self.W, self.H)
        self.root.geometry(self._window_geometry)
        self.root.configure(bg=T.BG)
        self.root.resizable(False, False)
        self.fullscreen = False

        self.sound = SoundManager()
        cfg = load_app_config()
        fx = cfg.get("sfx_volume")
        if isinstance(fx, (int, float)) and not isinstance(fx, bool):
            self.sound._volume = max(0.0, min(1.0, float(fx)))
        self.preview = os.environ.get("JARVIS2_PREVIEW") == "1"
        self.sound.set_enabled(bool(cfg.get("sfx_enabled", True)) and not self.preview)
        if self.preview and os.environ.get("JARVIS2_PREVIEW_SIZE"):
            pw, ph = (int(v) for v in os.environ["JARVIS2_PREVIEW_SIZE"].split("x"))
            self.W, self.H = pw, ph
            self._window_geometry = f"{pw}x{ph}+40+40"
            self._normal_size = (pw, ph)
            self.root.geometry(self._window_geometry)

        self.name = _first_name()
        self.tick = 0
        self.blink = True
        self.stats = {"cpu": 0.0, "ram": 0.0, "disk": 0.0, "battery": None, "charging": False, "up": 0.0, "down": 0.0}
        self.cpu_hist = [0.0] * 30
        self._net = psutil.net_io_counters()
        self._net_t = time.time()
        self._hits: list[tuple] = []
        self._hover = None
        self._mouse = (-1, -1)
        self._drawer = ""            # sağ sütunda sohbet yerine açık panel: "" | "settings" | "phone"
        self._phone_qr_url = None
        self._phone_window = None
        self._history_panel = None
        self._sources_dialog = None
        self._cam_label = None
        self._cam_photo = None
        self._last_cam_jpeg = None
        self._diag_last = None
        self._call_windows = {}
        # Asıl JARVIS'ten gelen ayar pencereleri (Telegram, Call Agent, bildirim eşikleri) "ui.root" ve
        # "ui.write_log" bekler; bu küçük nesne onlara yeter (pencere tutamaçları da üzerinde saklanır).
        self._legacy_ui = SimpleNamespace(
            root=self.root, write_log=lambda text: self.s.log(
                "err" if str(text).startswith("ERR") else "sys", re.sub(r"^(SYS|ERR):\s*", "", str(text))))

        self.c = tk.Canvas(self.root, width=self.W, height=self.H, bg=T.BG, highlightthickness=0)
        self.c.place(x=0, y=0)
        self._layout(self.W, self.H)
        self.orb = OrbView(self.W, self.H, T.display, T.bold)
        self._build_chat()
        self._build_settings()
        self._place_widgets()

        self.c.bind("<Button-1>", self._on_click)
        self.c.bind("<Motion>", lambda e: setattr(self, "_mouse", (e.x, e.y)))
        self.c.bind("<Leave>", lambda e: setattr(self, "_mouse", (-1, -1)))
        for key, fn in (("<F4>", self.toggle_mute), ("<Command-m>", self.toggle_mute), ("<F5>", self.toggle_pause),
                        ("<F6>", self.toggle_camera), ("<F7>", self.listen_now), ("<F8>", self.toggle_screen),
                        ("<Command-f>", self.toggle_fullscreen), ("<Command-b>", self.open_brain),
                        ("<Command-comma>", self.toggle_settings), ("<Escape>", self._esc)):
            self.root.bind(key, lambda e, f=fn: f())
        self.root.bind("<Configure>", self._on_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        try:
            self.root.createcommand("::tk::mac::Quit", self.shutdown)
        except Exception:
            pass
        self.on_shutdown = None

        self._update_stats_async()
        self.root.update_idletasks()
        self._draw()
        self.root.deiconify()
        self.root.update()
        if not self.preview:
            self.fullscreen = True
            self._enter_fullscreen()
            self.sound.play_startup()
        if not has_gemini_api_key() and not self.preview:
            self.root.after(600, self.open_api_key_dialog)
        if not self.preview:
            self.root.after(4000, self.core.phone.auto_start)
        self._animate()

    # ── Pencere ─────────────────────────────────────────────────────────────────────────────
    def _set_icon(self):
        for name in ("jarvis2_icon.png", "jarvis_logo_app.png", "jarvis_logo.png"):   # 04.10: yeni simge
            try:
                with Image.open(ICON_DIR / name) as logo:
                    self._app_icon = ImageTk.PhotoImage(logo.convert("RGBA").resize((256, 256), Image.LANCZOS),
                                                    master=self.root)
                self.root.iconphoto(True, self._app_icon)
                return
            except (OSError, tk.TclError):
                continue

    def _layout(self, width: int, height: int):
        self.W, self.H = int(width), int(height)
        W, H = self.W, self.H
        self.LEFT_W = max(250, min(318, int(W * 0.205)))
        self.RIGHT_W = max(300, min(392, int(W * 0.25)))
        self.FACE = face_size(W, H)                             # asıl JARVIS'teki küre boyutu
        cx0, cx1 = self.LEFT_W, W - self.RIGHT_W
        self.FCX = (cx0 + cx1) // 2
        self.DOCK_Y = H - FOOT_H - DOCK_H - 8
        avail_top, avail_bot = TOP_H, self.DOCK_Y - 4
        self.FCY = (avail_top + avail_bot) // 2
        # Sağ sütun: sohbet paneli
        self.CHAT_X, self.CHAT_Y = W - self.RIGHT_W + GAP // 2, TOP_H + GAP
        self.CHAT_W, self.CHAT_H = self.RIGHT_W - GAP - GAP // 2, H - TOP_H - FOOT_H - 2 * GAP

    def _on_configure(self, event):
        # Açılışta macOS kısa süre 1x1 boyut bildirir; o anki gerçek boyut 60 ms sonra okunur.
        if event.widget is self.root and (event.width, event.height) != (self.W, self.H):
            if getattr(self, "_resize_job", None):
                self.root.after_cancel(self._resize_job)
            self._resize_job = self.root.after(
                60, lambda: self._resize(self.root.winfo_width(), self.root.winfo_height()))

    def _resize(self, w, h):
        self._resize_job = None
        if (w, h) == (self.W, self.H) or w < 600 or h < 400:
            return
        self._layout(w, h)
        self.c.configure(width=w, height=h)
        self._place_widgets()
        for p in self.orb.particles:
            p["x"] %= w
            p["y"] %= h
        if self.s.webcam:
            self._camera_layout_apply()

    def _enter_fullscreen(self):
        self.root.resizable(True, True)
        self.root.attributes("-fullscreen", True)
        self._resize(self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _leave_fullscreen(self):
        self.root.attributes("-fullscreen", False)
        self.root.resizable(False, False)
        self.root.geometry(self._window_geometry)
        self._resize(*self._normal_size)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self._enter_fullscreen() if self.fullscreen else self._leave_fullscreen()

    def _esc(self):
        if self._drawer:                       # ayarlar / kayıtlar / telefon paneli açıksa önce o kapanır
            self._open_drawer("")
        elif self.fullscreen:
            self.fullscreen = False
            self._leave_fullscreen()
        else:
            self.shutdown()

    def shutdown(self):
        try:
            self.sound.stop_all()
        except Exception:
            pass
        if self.on_shutdown:
            try:
                self.on_shutdown()
            except Exception:
                pass
        try:
            self.root.destroy()
        finally:
            os._exit(0)

    def bring_to_front(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    # ── Sohbet ──────────────────────────────────────────────────────────────────────────────
    def _build_chat(self):
        self.chat = tk.Frame(self.root, bg=T.PANEL, highlightthickness=0)
        self.log = tk.Text(self.chat, fg=T.TEXT, bg=T.PANEL, insertbackground=T.TEXT, borderwidth=0,
                           highlightthickness=0, wrap="word", font=T.body(12), padx=4, pady=6, spacing2=3,
                           cursor="arrow")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        lg = self.log
        lg.tag_config("ai_name", foreground=T.CYAN, font=T.bold(10), spacing1=14, lmargin1=2)
        lg.tag_config("you_name", foreground=T.GOLD, font=T.bold(10), spacing1=14, justify="right", rmargin=2)
        lg.tag_config("stamp", foreground=T.TEXT_FAINT, font=T.body(9))
        lg.tag_config("ai", foreground=T.TEXT, background=T.BUBBLE_AI, font=T.body(12), lmargin1=12, lmargin2=12,
                      lmargincolor=T.BUBBLE_AI, rmargin=36, rmargincolor=T.PANEL, spacing1=8, spacing3=8)
        lg.tag_config("you", foreground="#f1ecd2", background=T.BUBBLE_YOU, font=T.body(12), lmargin1=36,
                      lmargin2=36, lmargincolor=T.PANEL, rmargin=12, rmargincolor=T.BUBBLE_YOU, spacing1=8,
                      spacing3=8, justify="right")
        lg.tag_config("sys", foreground="#6c8f99", font=T.body(10), spacing1=10, justify="center", lmargin1=10,
                      lmargin2=10, rmargin=10)
        lg.tag_config("err", foreground="#ff7b86", font=T.body(10), spacing1=10, justify="center", lmargin1=10,
                      lmargin2=10, rmargin=10)
        lg.tag_config("note", foreground=T.GOLD, font=T.body(11), spacing1=10, spacing3=4, lmargin1=10,
                      lmargin2=26, rmargin=10)

        self.input_frame = tk.Frame(self.root, bg=T.EDGE, highlightthickness=0)
        self.entry = tk.Entry(self.input_frame, bg=T.PANEL_HI, fg=T.TEXT, insertbackground=T.CYAN, borderwidth=0,
                              highlightthickness=0, font=T.body(12))
        self.entry.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1, ipady=7, ipadx=10)
        self.send_btn = tk.Label(self.input_frame, text="GÖNDER  ➤", bg=T.CYAN, fg=T.BG, font=T.bold(10),
                                 cursor="hand2", padx=12)
        self.send_btn.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.send_btn.bind("<Button-1>", lambda e: self._send())
        self.entry.bind("<Return>", lambda e: self._send())
        self._hint_on = False
        self.entry.bind("<FocusIn>", lambda e: self._hint(False))
        self.entry.bind("<FocusOut>", lambda e: self._hint(True))
        self._hint(True)

    def _hint(self, on: bool):
        if on and not self.entry.get():
            self._hint_on = True
            self.entry.configure(fg=T.TEXT_FAINT)
            self.entry.insert(0, "JARVIS'e yaz…")
        elif not on and self._hint_on:
            self._hint_on = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=T.TEXT)

    def _send(self):
        if self._hint_on:
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.core.submit_text(text)

    def _place_widgets(self):
        x, y, w, h = self.CHAT_X, self.CHAT_Y, self.CHAT_W, self.CHAT_H
        in_h = 40
        self.chat.place(x=x + 12, y=y + 46, width=w - 24, height=h - 46 - in_h - 30)
        self.input_frame.place(x=x + 12, y=y + h - in_h - 14, width=w - 24, height=in_h)
        if self._drawer:
            self.settings.place(x=x + 1, y=y + 40, width=w - 2, height=h - 41)
            self.settings.lift()

    def add_log(self, who: str, text: str):
        lg = self.log
        lg.configure(state="normal")
        stamp = time.strftime("%H:%M")
        if who == "ai":
            lg.insert("end", "JARVIS", "ai_name")
            lg.insert("end", "  ·  " + stamp + "\n", ("ai_name", "stamp"))
            lg.insert("end", text + "\n", "ai")
        elif who == "you":
            lg.insert("end", stamp + "  ·  ", ("you_name", "stamp"))
            lg.insert("end", "SİZ\n", "you_name")
            lg.insert("end", text + "\n", "you")
        elif who == "note":
            lg.insert("end", "🔔  " + stamp + "  ·  " + text + "\n", "note")
        else:
            lg.insert("end", ("⚠ " if who == "err" else "› ") + text + "\n", "err" if who == "err" else "sys")
        try:
            lines = int(lg.index("end-1c").split(".")[0])
            if lines > LOG_MAX_LINES:
                lg.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        except (tk.TclError, ValueError):
            pass
        lg.see("end")
        lg.configure(state="disabled")

    # ── Ayarlar paneli ──────────────────────────────────────────────────────────────────────
    def _build_settings(self):
        """Kaydırılabilir ayarlar paneli: dış çerçeve + tuval + içerik çerçevesi (fare tekerleğiyle kayar)."""
        self.settings = tk.Frame(self.root, bg=T.PANEL, highlightthickness=0)
        self._set_canvas = tk.Canvas(self.settings, bg=T.PANEL, highlightthickness=0, borderwidth=0)
        self._set_canvas.pack(fill="both", expand=True)
        self._set_inner = tk.Frame(self._set_canvas, bg=T.PANEL)
        self._set_win = self._set_canvas.create_window(0, 0, window=self._set_inner, anchor="nw")
        self._set_inner.bind("<Configure>", lambda e: self._set_canvas.configure(
            scrollregion=self._set_canvas.bbox("all")))
        self._set_canvas.bind("<Configure>", lambda e: self._set_canvas.itemconfigure(self._set_win, width=e.width))

        def wheel(e):
            if self._drawer:
                self._set_canvas.yview_scroll(int(-e.delta) if abs(e.delta) < 10 else int(-e.delta / 30), "units")
        self.root.bind_all("<MouseWheel>", wheel, add="+")

    def _rebuild_settings(self):
        f = self._set_inner
        for child in f.winfo_children():
            child.destroy()
        self._set_canvas.yview_moveto(0)
        cfg = load_app_config()

        def section(title):
            tk.Label(f, text=title, bg=T.PANEL, fg=T.CYAN, font=T.display(11), anchor="w").pack(
                fill="x", padx=16, pady=(16, 6))

        def note(text):
            tk.Label(f, text=text, bg=T.PANEL, fg=T.TEXT_FAINT, font=T.body(9), anchor="w", justify="left",
                     wraplength=self.CHAT_W - 40).pack(fill="x", padx=16, pady=(0, 4))

        def toggle(label, on, command):
            row = tk.Canvas(f, height=30, bg=T.PANEL, highlightthickness=0, cursor="hand2")
            row.pack(fill="x", padx=16, pady=2)

            def paint(value):
                row.delete("all")
                w = max(200, self.CHAT_W - 34)
                row.create_text(0, 15, text=label, fill=T.TEXT, font=T.body(11), anchor="w")
                x1 = w - 4
                col = T.CYAN if value else T.EDGE_HI
                row.create_rectangle(x1 - 40, 6, x1, 24, fill=T.dim(col, .35 if value else .6), outline=col)
                kx = x1 - 12 if value else x1 - 30
                row.create_rectangle(kx - 7, 9, kx + 7, 21, fill=col, outline="")
            state = {"v": bool(on)}
            paint(state["v"])

            def click(_e):
                state["v"] = not state["v"]
                paint(state["v"])
                command(state["v"])
            row.bind("<Button-1>", click)

        def action(label, command, color=T.TEXT):
            btn = tk.Label(f, text=label, bg=T.PANEL_HI, fg=color, font=T.bold(10), anchor="w", padx=12, pady=7,
                           cursor="hand2", highlightthickness=1, highlightbackground=T.EDGE)
            btn.pack(fill="x", padx=16, pady=3)
            btn.bind("<Button-1>", lambda e: command())
            btn.bind("<Enter>", lambda e: btn.configure(highlightbackground=T.CYAN))
            btn.bind("<Leave>", lambda e: btn.configure(highlightbackground=T.EDGE))

        section("SES")
        voice = str(cfg.get("voice", "Charon") or "Charon")
        grid = tk.Frame(f, bg=T.PANEL)
        grid.pack(fill="x", padx=16, pady=(0, 4))
        for i, v in enumerate(VOICES):
            chip = tk.Label(grid, text=v, bg=T.dim(T.CYAN, .3) if v == voice else T.PANEL_HI,
                            fg=T.CYAN if v == voice else T.TEXT_DIM, font=T.bold(10), pady=5, cursor="hand2",
                            highlightthickness=1, highlightbackground=T.CYAN if v == voice else T.EDGE)
            chip.grid(row=i // 4, column=i % 4, sticky="ew", padx=2, pady=2)
            chip.bind("<Button-1>", lambda e, name=v: self._choose_voice(name))
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1)
        note("JARVIS'in konuşma sesi. Seçince bağlantı yenilenir.")
        toggle("Efekt sesleri", bool(cfg.get("sfx_enabled", True)), self._set_sfx)
        scale = tk.Scale(f, from_=0, to=100, orient="horizontal", showvalue=False, bg=T.PANEL, fg=T.TEXT,
                         troughcolor=T.EDGE, highlightthickness=0, borderwidth=0, sliderrelief="flat",
                         activebackground=T.CYAN, command=self._set_sfx_volume, length=self.CHAT_W - 40)
        scale.set(int(self.sound.get_volume() * 100))
        scale.pack(fill="x", padx=16, pady=(0, 2))
        note("Efekt ses düzeyi")

        section("ZEKÂ")
        toggle("Daha dikkatli düşün", int(cfg.get("thinking_budget", 0) or 0) > 0, self._set_thinking)
        note("Açıkken JARVIS cevap vermeden önce kısaca düşünür: karmaşık isteklerde daha isabetli, "
             "cevaba başlaması biraz daha geç.")
        action("GEMİNİ API ANAHTARI  ·  " + ("kayıtlı ✓" if has_gemini_api_key() else "EKSİK"),
               self.open_api_key_dialog, T.TEXT if has_gemini_api_key() else T.RED)

        section("İKİNCİ BEYİN")
        action("◈  Kaynak klasörler", self.open_brain_sources)
        toggle("El kontrolü (kamera)", bool(cfg.get("hand_control_enabled", False)), self._set_hand_control)
        note("Kamera yalnızca İkinci Beyin penceresi açıkken çalışır.")

        section("GİZLİLİK VE GEÇMİŞ")
        toggle("Etkinlik geçmişi", bool(cfg.get("activity_history_enabled", True)),
               lambda v: self._save({"activity_history_enabled": v}, "Etkinlik geçmişi " + ("açıldı." if v else "kapatıldı.")))
        note("Yerel kayıt · 7 gün · yalnızca JARVIS açıkken.")
        action("↶  İşlem geçmişi ve geri alma", self.show_action_history)

        section("BİLDİRİMLER")
        from actions.proactive import settings as proactive_settings
        pro = proactive_settings()
        toggle("Proaktif bildirimler", pro["enabled"], lambda v: self._set_proactive("enabled", v))
        toggle("Bildirimleri sesli oku", pro["spoken"], lambda v: self._set_proactive("spoken", v))
        note("Düşük pil, yaklaşan etkinlik, uzun süren yüksek işlemci, disk ve yarınki çakışmalar. "
             "Sessiz saatlerde (varsayılan 23–08) susar. \"İndiğinde söyle\" gibi takipler de buradan okunur.")
        action("⚙  Eşikler ve sessiz saatler", self.open_proactive_settings)

        section("UZAKTAN ERİŞİM")
        from actions.telegram_bridge import settings as telegram_settings
        tg = telegram_settings()
        tg_on = bool(tg.get("enabled") and tg.get("token") and tg.get("owner_id"))
        action("✈  Telegram  ·  " + ("bağlı ✓" if tg_on else "kurulu değil"), self.open_telegram_settings)
        note("Kendi Telegram hesabından JARVIS 2'ye yazı ya da sesli not gönder (JARVIS 2 açıkken). "
             "Dosya/takvim değişikliklerinde tek kullanımlık onay kodu istenir.")
        action("📱  JARVIS Telefon (bağlantı ve eşleştirme)", self.toggle_phone)
        from actions.call_agent import settings as call_settings
        try:
            ca_on = bool(call_settings().get("enabled"))
        except (ValueError, OSError):
            ca_on = False
        action("☎  İşletme araması (Vapi)  ·  " + ("açık ✓" if ca_on else "kurulu değil"),
               self.open_call_agent_settings)
        note("\"Restoranı ara, yarın 20.00 için 2 kişilik yer ayırt\" gibi istekler. Vapi hesabı, numara ve "
             "Türkçe asistan gerekir; her arama başlamadan önce onay penceresi açılır.")

        section("UYGULAMA")
        from jarvis import autostart
        toggle("Bilgisayar açılınca başlat", autostart.is_enabled(), self._set_autostart)
        note("Oturum açılınca JARVIS 2 kendiliğinden açılır (bir sonraki açılıştan itibaren). Asıl JARVIS'in "
             "ayarına dokunmaz; ikisini birden açılışa koyma.")
        action("Masaüstü uygulamasını kur / yenile", self._build_app)

        section("SES KOMUTLARI")
        from jarvis import wake
        toggle("\"Hey Jarvis\" ile aç (JARVIS 2 kapalıyken)", wake.is_enabled(), self._set_wake)
        note("Açıkken arka planda küçük bir dinleyici \"Hey Jarvis\" ya da \"Jarvis açıl\" sözünü bekler ve "
             "JARVIS 2'yi açar. Ses yalnız bu Mac'te işlenir, kaydedilmez. JARVIS 2 ya da asıl JARVIS açıkken "
             "dinlemez. İlk açılışta macOS \"jarvis-python\" için mikrofon izni sorar.")
        note("İnternet ya da Gemini yokken temel komutlar yine çalışır: \"Safari'yi aç\", \"sesi azalt\", "
             "\"pil durumu\", \"saat kaç\", \"rapor.pdf'i bul\", \"son işlemi geri al\"…")
        tk.Frame(f, bg=T.PANEL, height=20).pack(fill="x")

    def _save(self, updates: dict, message: str = ""):
        try:
            save_app_config(updates)
            if message:
                self.s.log("sys", message)
            return True
        except OSError as exc:
            self.s.log("err", f"Ayar kaydedilemedi: {exc}")
            return False

    def _choose_voice(self, name):
        if self._save({"voice": name}, f"Ses: {name}. Bağlantı yenileniyor…"):
            self.core.request_reconnect()
            self._rebuild_settings()

    def _set_sfx(self, on):
        self._save({"sfx_enabled": on})
        self.sound.set_enabled(on)

    def _set_sfx_volume(self, value):
        vol = max(0, min(100, int(float(value)))) / 100.0
        self.sound.set_volume(vol)
        job = getattr(self, "_fx_job", None)
        if job:
            self.root.after_cancel(job)
        self._fx_job = self.root.after(400, lambda: self._save({"sfx_volume": vol}))

    def _set_thinking(self, on):
        if self._save({"thinking_budget": 1024 if on else 0},
                      "Dikkatli düşünme " + ("açıldı." if on else "kapatıldı.") + " Bağlantı yenileniyor…"):
            self.core.request_reconnect()

    def _set_hand_control(self, on):
        if self._save({"hand_control_enabled": on}, "El kontrolü " + (
                "açıldı; kamera yalnızca İkinci Beyin penceresi açıkken çalışır." if on else "kapatıldı.")):
            self.core.brain.set_hand_control(on)

    def _set_proactive(self, key, value):
        from actions.proactive import settings as proactive_settings
        cfg = proactive_settings()
        cfg[key] = bool(value)
        self._save({"proactive": cfg}, {"enabled": "Proaktif bildirimler ", "spoken": "Sesli bildirim "}[key]
                   + ("açıldı." if value else "kapatıldı."))

    def open_proactive_settings(self):
        from actions.proactive import open_settings
        open_settings(self._legacy_ui)

    def open_telegram_settings(self):
        from telegram_ui import open_telegram_settings
        open_telegram_settings(self._legacy_ui)

    def open_call_agent_settings(self):
        from call_agent_ui import open_call_agent_settings
        open_call_agent_settings(self._legacy_ui)

    def _show_call_review(self, proposal, answer):
        from call_agent_ui import show_call_review
        self.bring_to_front()
        key = proposal.get("id")

        def decided(ok):
            self._call_windows.pop(key, None)
            answer(ok)
        try:
            self._call_windows[key] = show_call_review(self._legacy_ui, proposal, decided)
        except Exception as exc:
            self.s.log("err", f"Arama onay penceresi açılamadı: {exc}")
            answer(False)

    def _close_call_review(self, key):
        win = self._call_windows.pop(key, None)
        if win is not None and win.winfo_exists():
            win.destroy()

    def _set_autostart(self, on):
        from jarvis import autostart
        try:
            if on:
                autostart.enable()
                self.s.log("sys", "Bilgisayar açılınca JARVIS 2 kendiliğinden başlayacak. macOS bir \"arka plan "
                                  "öğesi eklendi\" bildirimi gösterebilir.")
            else:
                autostart.disable()
                self.s.log("sys", "Açılışta başlatma kapatıldı.")
        except (OSError, RuntimeError) as exc:
            self.s.log("err", f"Açılışta başlatma ayarlanamadı: {exc}")
            if self._settings_open:
                self._rebuild_settings()

    def _set_wake(self, on):
        def run():
            from jarvis import wake
            try:
                if on:
                    wake.install()
                    self.s.log("sys", "\"Hey Jarvis\" açıldı: JARVIS 2 kapalıyken \"Hey Jarvis\" demen yeterli. "
                                      "macOS mikrofon izni sorarsa izin ver.")
                else:
                    wake.uninstall()
                    self.s.log("sys", "\"Hey Jarvis\" kapatıldı; arka plan dinleyicisi kaldırıldı.")
            except Exception as exc:
                self.s.log("err", f"\"Hey Jarvis\" ayarlanamadı: {exc}")
                self.s.post("settings_refresh")
        threading.Thread(target=run, daemon=True).start()

    def _camera_auth(self, index, reply):
        """Ana iş parçacığında kamerayı bir kez açar: OpenCV macOS izin penceresini yalnız buradan gösterebiliyor.
        Kullanıcı cevap verene kadar arayüz bekler (sistem penceresi). Sonra kamera kapatılır; akış arka planda açılır."""
        self.bring_to_front()
        try:
            import cv2
            cap = cv2.VideoCapture(int(index), cv2.CAP_AVFOUNDATION)
            cap.release()
        except Exception as exc:
            print(f"[Webcam] İzin penceresi açılamadı: {exc}", flush=True)
        try:
            from brain.hand_tracker import camera_auth_status
            status = camera_auth_status()
        except Exception:
            status = None
        reply(3 if status is None else status)

    def _build_app(self):
        def run():
            try:
                from jarvis.app_bundle import create_app
                path = create_app()
                self.s.log("sys", f"Masaüstü uygulaması hazır: {path.name} (JARVIS 2 klasöründe).")
            except Exception as exc:
                self.s.log("err", f"Uygulama kurulamadı: {exc}")
        threading.Thread(target=run, daemon=True).start()

    @property
    def _settings_open(self) -> bool:
        return self._drawer == "settings"

    def toggle_settings(self):
        self._open_drawer("" if self._drawer in ("settings", "logs") else "settings")

    def toggle_phone(self):
        self._open_drawer("" if self._drawer == "phone" else "phone")

    def _open_drawer(self, kind: str):
        # macOS Tk'de kaydırılabilir paneldeki (tuvale gömülü) düğmeler panel gizlenince ekrandan silinmiyor,
        # sohbetin üstünde/altında kalıyordu: içerik her seferinde tamamen kaldırılır.
        for child in self._set_inner.winfo_children():
            child.destroy()
        self._drawer = kind
        self._phone_qr_url = None
        if kind == "settings":
            self._rebuild_settings()
        elif kind == "phone":
            self._rebuild_phone()
        elif kind == "logs":
            self._rebuild_logs()
        if kind:
            self._place_widgets()
        else:
            self.settings.place_forget()
            self.chat.lift()
            self.input_frame.lift()

    # ── Kayıtlar paneli ─────────────────────────────────────────────────────────────────────
    LOG_FILES = (("app", "JARVIS 2", "jarvis2.log"), ("wake", "Hey Jarvis", "wake.log"))
    LOG_TAIL = 500

    def _rebuild_logs(self):
        """Teknik günlüğün son satırları (~/Library/Logs/JARVIS 2). Açıkken 2 sn'de bir yenilenir; yukarı
        kaydırılmışsa yerinde kalır. Yalnız okunur; günlük bu Mac'ten çıkmaz."""
        f = self._set_inner
        self._set_canvas.yview_moveto(0)
        self._log_which = getattr(self, "_log_which", "app")
        bar = tk.Frame(f, bg=T.PANEL)
        bar.pack(fill="x", padx=16, pady=(10, 6))

        def chip(text, cmd, on=False):
            lbl = tk.Label(bar, text=text, bg=T.dim(T.CYAN, .3) if on else T.PANEL_HI,
                           fg=T.CYAN if on else T.TEXT_DIM, font=T.bold(9), padx=7, pady=5, cursor="hand2",
                           highlightthickness=1, highlightbackground=T.CYAN if on else T.EDGE)
            lbl.pack(side="left", padx=(0, 5))
            lbl.bind("<Button-1>", lambda e: cmd())
            return lbl
        for key, label, _ in self.LOG_FILES:
            chip(label, lambda k=key: self._show_log(k), self._log_which == key)
        chip("↻ Yenile", lambda: self._fill_log(force=True))
        chip("Klasör", lambda: os.system(f"open {LOG_DIR.as_posix()!r} >/dev/null 2>&1 &"))
        self._log_note = tk.Label(f, text="", bg=T.PANEL, fg=T.TEXT_FAINT, font=T.body(9), anchor="w")
        self._log_note.pack(fill="x", padx=16)
        text = tk.Text(f, bg=T.BG, fg=T.TEXT_DIM, font=("Menlo", 10), wrap="word", borderwidth=0,
                       highlightthickness=1, highlightbackground=T.EDGE, padx=8, pady=6,
                       height=max(8, int((self.CHAT_H - 140) / 15)))
        text.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        for tag, col in (("tool", T.CYAN), ("err", "#ff7b86"), ("sys", T.GOLD), ("dim", T.TEXT_FAINT)):
            text.tag_config(tag, foreground=col)

        def wheel(e):
            text.yview_scroll(int(-e.delta) if abs(e.delta) < 10 else int(-e.delta / 30), "units")
            return "break"                      # dış panel kaymasın
        text.bind("<MouseWheel>", wheel)
        text.configure(state="disabled")
        self._log_text, self._log_sig = text, None
        self._fill_log(force=True)

    def _show_log(self, which):
        self._log_which = which
        self._open_drawer("logs")

    @staticmethod
    def _log_tag(line: str) -> str:
        low = line.lower()
        if "traceback" in low or "error" in low or "hata" in low or "⚠" in line or "kopt" in low:
            return "err"
        if line.startswith("[ARAÇ]"):
            return "tool"
        if line.startswith(("[BAĞLANTI]", "[SES]", "[MİKROFON]", "===")):
            return "sys"
        return ""

    def _fill_log(self, force=False):
        text = getattr(self, "_log_text", None)
        if self._drawer != "logs" or text is None or not text.winfo_exists():
            return
        name = dict((k, f) for k, _, f in self.LOG_FILES)[self._log_which]
        path = LOG_DIR / name
        try:
            st = path.stat()
            sig = (st.st_size, st.st_mtime)
        except OSError:
            sig = None
        if force or sig != self._log_sig:
            self._log_sig = sig
            if sig is None:
                lines = ["Henüz kayıt yok. JARVIS 2 uygulamadan açıldığında burada görünür."
                         if self._log_which == "app" else "\"Hey Jarvis\" açılmadıysa bu kayıt boştur."]
            else:
                with open(path, "rb") as fh:
                    fh.seek(max(0, sig[0] - 400_000))
                    lines = fh.read().decode("utf-8", "replace").splitlines()[-self.LOG_TAIL:]
            at_end = text.yview()[1] >= 0.999
            text.configure(state="normal")
            text.delete("1.0", "end")
            for line in lines:
                text.insert("end", line + "\n", self._log_tag(line))
            text.configure(state="disabled")
            if at_end or force:
                text.see("end")
                self.root.after(80, lambda: text.winfo_exists() and text.see("end"))   # ilk yerleşimden sonra
            self._log_note.configure(text=f"{path}  ·  son {len(lines)} satır" if sig else str(path))
        self.root.after(2000, self._fill_log)

    # ── Telefon paneli ──────────────────────────────────────────────────────────────────────
    def _rebuild_phone(self):
        f = self._set_inner
        self._set_canvas.yview_moveto(0)
        ph = self.core.phone
        wrap = self.CHAT_W - 40

        def label(text, fg=T.TEXT_DIM, font=None, pady=(0, 4)):
            lb = tk.Label(f, text=text, bg=T.PANEL, fg=fg, font=font or T.body(10), anchor="w", justify="left",
                          wraplength=wrap)
            lb.pack(fill="x", padx=16, pady=pady)
            return lb

        def button(text, command, fg=T.TEXT):
            b = tk.Label(f, text=text, bg=T.PANEL_HI, fg=fg, font=T.bold(11), pady=9, cursor="hand2",
                         highlightthickness=1, highlightbackground=T.EDGE)
            b.pack(fill="x", padx=16, pady=4)
            b.bind("<Button-1>", lambda e: command())
            b.bind("<Enter>", lambda e: b.configure(highlightbackground=T.CYAN))
            b.bind("<Leave>", lambda e: b.configure(highlightbackground=T.EDGE))
            return b

        tk.Frame(f, bg=T.PANEL, height=10).pack(fill="x")
        self._ph_main = label("", T.TEXT, T.display(15), (6, 2))
        self._ph_sub = label("")
        self._ph_toggle = button("", ph.toggle)
        button("📱  ANDROID UYGULAMASINI EŞLEŞTİR", self._open_pairing, T.CYAN)
        label("Telefondaki JARVIS, Mac'e bu bağlantıyla ulaşır: Mac araçlarını kullanır, ortak hafızayı ve "
              "\"ara ve söyle\" özelliğini paylaşır.", T.TEXT_FAINT, T.body(9), (6, 10))
        tk.Label(f, text="TARAYICIDAN BAĞLAN", bg=T.PANEL, fg=T.CYAN, font=T.display(11), anchor="w").pack(
            fill="x", padx=16, pady=(10, 6))
        self._ph_qr = tk.Label(f, bg=T.PANEL, fg=T.TEXT_FAINT, font=T.body(10), justify="center",
                               text="Bağlantı açılınca burada QR kodu görünür.")
        self._ph_qr.pack(padx=16, pady=4)
        row = tk.Frame(f, bg=T.PANEL)
        row.pack(fill="x", padx=16, pady=(2, 6))
        self._ph_copy = tk.Label(row, text="⧉  Erişim anahtarını kopyala", bg=T.PANEL_HI, fg=T.TEXT_DIM,
                                 font=T.bold(10), pady=6, cursor="hand2")
        self._ph_copy.pack(fill="x")
        self._ph_copy.bind("<Button-1>", lambda e: self._copy_phone_token())
        tk.Label(f, text="OTOMATİK", bg=T.PANEL, fg=T.CYAN, font=T.display(11), anchor="w").pack(
            fill="x", padx=16, pady=(12, 6))
        auto = tk.Canvas(f, height=30, bg=T.PANEL, highlightthickness=0, cursor="hand2")
        auto.pack(fill="x", padx=16)
        state = {"v": bool(load_app_config().get("phone_autostart", True))}

        def paint():
            auto.delete("all")
            w = max(200, self.CHAT_W - 34)
            auto.create_text(0, 15, text="Açılışta kendiliğinden başlat", fill=T.TEXT, font=T.body(11), anchor="w")
            col = T.CYAN if state["v"] else T.EDGE_HI
            auto.create_rectangle(w - 44, 6, w - 4, 24, fill=T.dim(col, .35 if state["v"] else .6), outline=col)
            kx = w - 16 if state["v"] else w - 34
            auto.create_rectangle(kx - 7, 9, kx + 7, 21, fill=col, outline="")

        def click(_e):
            state["v"] = not state["v"]
            paint()
            self._save({"phone_autostart": state["v"]})
        auto.bind("<Button-1>", click)
        paint()
        label("Yalnız Tailscale açıkken (sabit adres). Tailscale yoksa geçici adres her açılışta değişeceği için "
              "elle başlatılır.", T.TEXT_FAINT, T.body(9), (0, 6))
        label("⚠ Asıl JARVIS'in telefon bağlantısı açıksa JARVIS 2'ninki açılamaz (aynı adres). Telefon, hangisi "
              "açıksa ona bağlanır; yeniden eşleştirme gerekmez.", T.TEXT_FAINT, T.body(9), (8, 20))
        self._update_phone_drawer()

    def _update_phone_drawer(self):
        if self._drawer != "phone":
            return
        ph = self.core.phone
        main, sub = ph.summary()
        col = T.CYAN if ph.running and ph.connected else T.GOLD if ph.busy or ph.running else T.TEXT_DIM
        try:
            self._ph_main.configure(text=main, fg=col)
            self._ph_sub.configure(text=sub + (f"\nDurum: {ph.status}" if ph.status and ph.status != "kapalı" else "")
                                   + (f"\n{ph.notice}" if ph.notice and ph.running else ""))
            if ph.busy:
                self._ph_toggle.configure(text="LÜTFEN BEKLE…", fg=T.GOLD)
            elif ph.running:
                self._ph_toggle.configure(text="■  DURDUR", fg=T.RED)
            else:
                self._ph_toggle.configure(text="▶  BAŞLAT", fg=T.CYAN)
            url = ph.url if ph.running else ""
            if url != self._phone_qr_url:
                self._phone_qr_url = url
                self._render_qr(url)
            self._ph_copy.configure(fg=T.CYAN if ph.token else T.TEXT_FAINT)
        except tk.TclError:
            pass

    def _render_qr(self, url):
        if not url:
            self._ph_qr.configure(image="", text="Bağlantı açılınca burada QR kodu görünür.")
            self._qr_photo = None
            return
        try:
            import qrcode
            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#102227", back_color="#def8ff").convert("RGB")
            size = min(220, self.CHAT_W - 80)
            self._qr_photo = ImageTk.PhotoImage(img.resize((size, size), Image.NEAREST))
            self._ph_qr.configure(image=self._qr_photo, text="")
        except Exception as exc:
            self._ph_qr.configure(image="", text=f"QR üretilemedi: {exc}")

    def _copy_phone_token(self):
        token = self.core.phone.token
        if not token:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(token)
        self._ph_copy.configure(text="✓  Kopyalandı")
        self.root.after(1400, lambda: self._ph_copy.winfo_exists() and self._ph_copy.configure(
            text="⧉  Erişim anahtarını kopyala"))

    def _open_pairing(self):
        win = self._phone_window
        if win is not None and win._alive():
            win.window.lift()
            return
        self._phone_window = self.core.phone.pairing_window(self.root)

    def open_api_key_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Gemini API anahtarı")
        win.configure(bg=T.PANEL)
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text="GEMİNİ API ANAHTARI", bg=T.PANEL, fg=T.CYAN, font=T.display(13)).pack(
            anchor="w", padx=20, pady=(18, 4))
        tk.Label(win, text="Anahtar yalnızca bu Mac'te, JARVIS 2'nin ayar dosyasında saklanır.\n"
                           "Google AI Studio › Get API key bölümünden alabilirsin.",
                 bg=T.PANEL, fg=T.TEXT_DIM, font=T.body(10), justify="left").pack(anchor="w", padx=20)
        entry = tk.Entry(win, show="•", width=46, bg=T.PANEL_HI, fg=T.TEXT, insertbackground=T.CYAN,
                         borderwidth=0, highlightthickness=1, highlightbackground=T.EDGE, font=T.body(12))
        entry.pack(padx=20, pady=12, ipady=6)
        entry.focus_set()

        def save(_e=None):
            key = entry.get().strip()
            if len(key) < 20:
                entry.configure(highlightbackground=T.RED)
                return
            if self._save({"gemini_api_key": key}, "Gemini API anahtarı kaydedildi; bağlanılıyor…"):
                win.destroy()
                self.core.request_reconnect()
                if self._settings_open:
                    self._rebuild_settings()
        entry.bind("<Return>", save)
        btn = tk.Label(win, text="KAYDET", bg=T.CYAN, fg=T.BG, font=T.bold(11), padx=18, pady=6, cursor="hand2")
        btn.pack(anchor="e", padx=20, pady=(0, 18))
        btn.bind("<Button-1>", save)

    # ── Eylemler ────────────────────────────────────────────────────────────────────────────
    def toggle_mute(self):
        self.core.set_muted(not self.s.muted)

    def toggle_pause(self):
        paused = not self.s.paused
        self.core.set_paused(paused)
        if paused:
            self.sound.stop_thinking()
        self.s.log("sys", "Duraklatıldı. Devam etmek için küreye tıkla ya da F5'e bas." if paused
                   else "Devam ediyorum; dinliyorum.")

    def listen_now(self):
        self.core.listen_now()

    def toggle_camera(self):
        self.core.toggle_webcam_async(not self.s.webcam)

    def toggle_screen(self):
        self.core.toggle_screen_watch_async(not self.s.screen_watch)

    def open_brain(self):
        threading.Thread(target=self.core.brain.open, daemon=True).start()

    def open_brain_sources(self):
        d = self._sources_dialog
        if d is not None and d.alive():
            d.win.lift()
            d.refresh()
            return
        from brain.sources_dialog import SourcesDialog
        self._sources_dialog = SourcesDialog(self.root, on_reindex=self.core.brain.start_index,
                                             on_open_graph=lambda focus="": self.core.brain.open(focus))

    def show_action_history(self):
        panel = self._history_panel
        if panel is None or not panel.alive:
            from action_history_ui import ActionHistoryPanel
            self._history_panel = ActionHistoryPanel(self.root, self._report_undo)
        else:
            panel.window.deiconify()
            panel.window.lift()
            panel.refresh()

    def _report_undo(self, result):
        message, lbl = result.get("message", ""), result.get("label", "")
        if result.get("status") in ("ok", "already_done"):
            self.s.log("sys", f"{lbl} — {message}" if lbl else message)
            self.sound.play_success()
        else:
            self.s.log("err", message)

    def _on_click(self, event):
        for x0, y0, x1, y1, key in reversed(self._hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                return self._activate(key)
        box = self.orb.box
        if box:
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if math.hypot(event.x - cx, event.y - cy) < box[4] * 1.02:
                if self.s.browsing or self.s.browser_visible:
                    self.core.toggle_browser()      # araştırma sürerken: Chrome'u göster / gizle
                else:
                    self.toggle_pause()

    def _activate(self, key):
        {"mic": self.toggle_mute, "pause": self.toggle_pause, "cam": self.toggle_camera,
         "screen": self.toggle_screen, "brain": self.open_brain, "settings": self.toggle_settings,
         "power": self.shutdown, "history": self.show_action_history, "fullscreen": self.toggle_fullscreen,
         "phone": self.toggle_phone, "close_drawer": lambda: self._open_drawer(""),
         "tab_settings": lambda: self._open_drawer("settings"), "tab_logs": lambda: self._open_drawer("logs"),
         }.get(key, lambda: None)()

    # ── Olaylar ve canlandırma ──────────────────────────────────────────────────────────────
    def _handle_events(self):
        for ev in self.s.drain():
            kind = ev[0]
            if kind == "log":
                self.add_log(ev[1], ev[2])
            elif kind == "status":
                prev, new = ev[1], ev[2]
                if new == "THINKING":
                    self.sound.start_thinking()
                elif prev == "THINKING":
                    self.sound.stop_thinking()
                if new == "ERROR" and prev != "ERROR":
                    self.sound.play_error()
            elif kind == "sfx":
                self.sound.play_success()
            elif kind == "show_action_history":
                self.show_action_history()
            elif kind == "refresh_action_history":
                if self._history_panel is not None and self._history_panel.alive:
                    self._history_panel.refresh()
            elif kind == "open_brain_sources":
                self.open_brain_sources()
            elif kind == "brain" and ev[1] in ("index_done", "index_error", "sources_changed", "indexed"):
                d = self._sources_dialog
                if d is not None and d.alive():
                    d.refresh()
            elif kind == "brain" and ev[1] == "hand_control" and self._settings_open:
                self._rebuild_settings()
            elif kind == "preview_settings":
                self.toggle_settings()
            elif kind == "settings_refresh":
                if self._settings_open:
                    self._rebuild_settings()
            elif kind == "preview_scroll":
                self._set_canvas.yview_moveto(ev[1])
            elif kind == "preview_logs":
                self._open_drawer("logs")
            elif kind == "preview_phone":
                self.toggle_phone()
            elif kind == "call_review":
                self._show_call_review(ev[1], ev[2])
            elif kind == "call_review_close":
                self._close_call_review(ev[1])
            elif kind == "open_call_agent":
                self.bring_to_front()
                self.open_call_agent_settings()
            elif kind == "camera_auth":
                self._camera_auth(ev[1], ev[2])
            elif kind == "confirm":
                from tkinter import messagebox
                _, title, text, answer = ev
                self.bring_to_front()
                answer(messagebox.askyesno(title, text, parent=self.root))
        s = self.s
        if s.status == "ERROR" and time.time() > s.error_hold_until and not s.speaking:
            s.set_status("LISTENING" if s.cloud else "INITIALISING")
        if s.status == "THINKING" and not s.tool and not self.core.response_pending and not s.speaking \
                and time.time() > s.user_speaking_until + 8:
            s.set_status("LISTENING")

    def _update_stats_async(self):
        def run():
            try:
                st = self.stats
                st["cpu"] = psutil.cpu_percent(interval=None)
                st["ram"] = psutil.virtual_memory().percent
                st["disk"] = psutil.disk_usage("/").percent
                batt = psutil.sensors_battery()
                st["battery"] = batt.percent if batt else None
                st["charging"] = bool(batt and batt.power_plugged)
                now, net = time.time(), psutil.net_io_counters()
                dt = now - self._net_t
                if dt > 0:
                    st["up"] = max(0.0, (net.bytes_sent - self._net.bytes_sent) / dt / 1024)
                    st["down"] = max(0.0, (net.bytes_recv - self._net.bytes_recv) / dt / 1024)
                self._net, self._net_t = net, now
                self.cpu_hist = self.cpu_hist[1:] + [st["cpu"]]
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _sync_browser_view(self):
        """JARVIS Chrome penceresi (Chrome araştırma ajanının canlı görüntüsü; küreye tıklayınca açılır/kapanır)."""
        try:
            if not hasattr(self, "browser_view"):
                from jarvis.ui.browser_view import BrowserView
                self.browser_view = BrowserView(self.root, self.core)
            self.browser_view.sync()
        except Exception as exc:          # pencere hatası kürenin çizimini asla durdurmasın
            if not getattr(self, "_browser_view_err", False):
                self._browser_view_err = True
                print(f"[ARAYÜZ] JARVIS Chrome penceresi: {type(exc).__name__}: {exc}", flush=True)

    def _animate(self):
        self.tick += 1
        if self.tick % 60 == 0:
            self._update_stats_async()
        if self.tick % 15 == 0:
            self._update_phone_drawer()
        if self.tick % 19 == 0:
            self.blink = not self.blink
        self._handle_events()
        self._sync_browser_view()
        self.orb.step(self.s, self.W, self.H, self.s.webcam)
        self._sync_camera()
        t0 = time.perf_counter()
        try:
            self._draw()
        except Exception as exc:
            print(f"[ARAYÜZ] Çizim hatası: {type(exc).__name__}: {exc}", flush=True)
        draw_s = time.perf_counter() - t0
        prev, self._diag_last = self._diag_last, t0
        if prev is not None and t0 - prev > 0.08 and self.tick > 50:
            if t0 - getattr(self, "_diag_logged", 0.0) > 3.0:
                self._diag_logged = t0
                print(f"[ARAYÜZ] takılma: {(t0 - prev) * 1000:.0f} ms (çizim {draw_s * 1000:.1f} ms)", flush=True)
        self.root.after(max(10, int(33 - draw_s * 1000)), self._animate)

    # ── Kamera önizlemesi ───────────────────────────────────────────────────────────────────
    def _camera_layout(self):
        center_w = (self.W - self.RIGHT_W) - self.LEFT_W
        cam_w = min(center_w - 40, 580)
        cam_h = int(cam_w * 9 / 16)
        remaining = (self.DOCK_Y - TOP_H) - cam_h - 24
        new_face = max(120, min(int(remaining * 0.82), int(center_w * 0.70), 520))
        new_cy = TOP_H + cam_h + 16 + remaining // 2
        return cam_w, cam_h, self.FCX - cam_w // 2, TOP_H + 8, new_cy - self.FCY, new_face

    def _camera_layout_apply(self):
        cam_w, cam_h, cam_x, cam_y, shift, face = self._camera_layout()
        self.orb.set_camera(True, shift, face, self.FACE)
        if self._cam_label is not None:
            self._cam_label.place(x=cam_x, y=cam_y, width=cam_w, height=cam_h)

    def _sync_camera(self):
        active = self.s.webcam
        if active and self.orb.cam_face_target == 0.0:
            self._camera_layout_apply()
        elif not active and self.orb.cam_shift_target != 0.0:
            self.orb.set_camera(False, 0, 0, self.FACE)
            if self._cam_label is not None:
                self._cam_label.place_forget()
            self._cam_photo = None
        if not active or self.tick % 2:
            return
        jpeg = self.core.webcam.get_latest_frame()
        if not jpeg or jpeg is self._last_cam_jpeg:
            return
        self._last_cam_jpeg = jpeg
        cam_w, cam_h, cam_x, cam_y, _, _ = self._camera_layout()
        try:
            img = Image.open(io.BytesIO(jpeg))
            iw, ih = img.size
            target = cam_w / cam_h
            if iw / ih > target:
                nw = int(ih * target)
                img = img.crop(((iw - nw) // 2, 0, (iw + nw) // 2, ih))
            else:
                nh = int(iw / target)
                img = img.crop((0, (ih - nh) // 2, iw, (ih + nh) // 2))
            photo = ImageTk.PhotoImage(img.resize((cam_w, cam_h), Image.LANCZOS))
            if self._cam_label is None:
                self._cam_label = tk.Label(self.root, bg=T.BG, highlightthickness=1, highlightbackground=T.EDGE_HI)
            self._cam_label.configure(image=photo)
            self._cam_photo = photo
            self._cam_label.place(x=cam_x, y=cam_y, width=cam_w, height=cam_h)
            self._cam_label.lift(self.c)
        except Exception as exc:
            print(f"[ARAYÜZ] Kamera önizlemesi: {exc}", flush=True)

    # ── Çizim yardımcıları ──────────────────────────────────────────────────────────────────
    def _hit(self, x0, y0, x1, y1, key):
        self._hits.append((x0, y0, x1, y1, key))
        mx, my = self._mouse
        return x0 <= mx <= x1 and y0 <= my <= y1

    def _panel(self, x, y, w, h, title="", accent=T.CYAN, right_text="", right_color=T.TEXT_FAINT):
        c, cut = self.c, 12
        pts = (x + cut, y, x + w, y, x + w, y + h - cut, x + w - cut, y + h, x, y + h, x, y + cut)
        c.create_polygon(*pts, fill=T.PANEL, outline=T.EDGE)
        c.create_line(x, y + cut + 16, x, y + cut, x + cut, y, x + cut + 16, y, fill=accent, width=2)
        c.create_line(x + w - cut - 16, y + h, x + w - cut, y + h, x + w, y + h - cut, x + w, y + h - cut - 16,
                      fill=T.dim(accent, .55), width=2)
        if title:
            c.create_text(x + 18, y + 22, text=title, fill=accent, font=T.display(11), anchor="w")
            c.create_line(x + 18, y + 38, x + w - 18, y + 38, fill=T.EDGE)
            if right_text:
                c.create_text(x + w - 18, y + 22, text=right_text, fill=right_color, font=T.bold(9), anchor="e")

    def _gauge(self, cx, cy, r, pct, color, label, value):
        c = self.c
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=225, extent=-270, style="arc", outline=T.EDGE, width=6)
        ext = -270 * max(0.0, min(1.0, pct / 100.0))
        if ext < -1:
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=225, extent=ext, style="arc", outline=color, width=6)
        c.create_text(cx, cy, text=value, fill=T.TEXT, font=T.display(13))
        c.create_text(cx, cy + r + 11, text=label, fill=T.TEXT_DIM, font=T.bold(8))

    def _icon(self, kind, cx, cy, col, s=11, crossed=False):
        c = self.c
        if kind == "mic":
            c.create_rectangle(cx - s * .32, cy - s, cx + s * .32, cy + s * .25, outline=col, width=2)
            c.create_arc(cx - s * .65, cy - s * .55, cx + s * .65, cy + s * .62, start=200, extent=140, style="arc",
                         outline=col, width=2)
            c.create_line(cx, cy + s * .62, cx, cy + s * .95, fill=col, width=2)
            if crossed:
                c.create_line(cx - s * .85, cy - s, cx + s * .85, cy + s * .95, fill=col, width=2)
        elif kind == "pause":
            if crossed:          # duraklatılmışken "oynat" üçgeni
                c.create_polygon(cx - s * .45, cy - s * .7, cx + s * .65, cy, cx - s * .45, cy + s * .7, fill=col,
                                 outline="")
            else:
                c.create_rectangle(cx - s * .55, cy - s * .7, cx - s * .15, cy + s * .7, fill=col, outline="")
                c.create_rectangle(cx + s * .15, cy - s * .7, cx + s * .55, cy + s * .7, fill=col, outline="")
        elif kind == "cam":
            c.create_rectangle(cx - s, cy - s * .55, cx + s * .35, cy + s * .55, outline=col, width=2)
            c.create_polygon(cx + s * .35, cy - s * .1, cx + s, cy - s * .5, cx + s, cy + s * .5, cx + s * .35,
                             cy + s * .1, fill=col, outline="")
        elif kind == "screen":
            c.create_rectangle(cx - s, cy - s * .7, cx + s, cy + s * .45, outline=col, width=2)
            c.create_line(cx, cy + s * .45, cx, cy + s * .85, fill=col, width=2)
            c.create_line(cx - s * .5, cy + s * .85, cx + s * .5, cy + s * .85, fill=col, width=2)
        elif kind == "brain":
            pts = [(cx - s * .7, cy - s * .45), (cx + s * .65, cy - s * .65), (cx + s * .2, cy + s * .7),
                   (cx - s * .5, cy + s * .55), (cx + s * .05, cy - s * .05)]
            for a, b in ((0, 4), (1, 4), (2, 4), (3, 4), (0, 3), (1, 2)):
                c.create_line(*pts[a], *pts[b], fill=T.dim(col, .7), width=1)
            for i, (x, y) in enumerate(pts):
                r = s * (.26 if i == 4 else .18)
                c.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="")
        elif kind == "gear":
            for k in range(8):
                a = math.radians(k * 45)
                c.create_line(cx + math.cos(a) * s * .55, cy + math.sin(a) * s * .55, cx + math.cos(a) * s,
                              cy + math.sin(a) * s, fill=col, width=3)
            c.create_oval(cx - s * .62, cy - s * .62, cx + s * .62, cy + s * .62, outline=col, width=2)
            c.create_oval(cx - s * .22, cy - s * .22, cx + s * .22, cy + s * .22, outline=col, width=2)
        elif kind == "power":
            c.create_arc(cx - s * .8, cy - s * .75, cx + s * .8, cy + s * .85, start=120, extent=300, style="arc",
                         outline=col, width=2)
            c.create_line(cx, cy - s, cx, cy, fill=col, width=2)
        elif kind == "full":
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                x, y = cx + dx * s * .8, cy + dy * s * .8
                c.create_line(x, y, x - dx * s * .45, y, fill=col, width=2)
                c.create_line(x, y, x, y - dy * s * .45, fill=col, width=2)

    # ── Çizim ───────────────────────────────────────────────────────────────────────────────
    def _draw(self):
        c, s = self.c, self.s
        c.delete("all")
        self._hits = []
        self.orb.draw_particles(c, s)
        face = self.FACE
        self.orb.draw(c, s, self.FCX, self.FCY, face, cam=s.webcam)
        self._draw_top()
        self._draw_left()
        self._draw_dock()
        self._draw_chat_frame()
        self._draw_footer()

    def _conn(self):
        s = self.s
        if s.paused:
            return "DURAKLATILDI", T.TEXT_DIM
        if not has_gemini_api_key():
            return "ANAHTAR YOK", T.RED
        if s.cloud:
            return "ÇEVRİMİÇİ", T.CYAN
        return "BAĞLANIYOR", T.GOLD

    def _draw_top(self):
        c, W = self.c, self.W
        y = TOP_H - 1
        c.create_line(16, y, W - 16, y, fill=T.EDGE)
        cx = self.FCX
        c.create_line(cx - 160, y, cx - 148, y - 7, cx + 148, y - 7, cx + 160, y, fill=T.TEAL, width=2)
        # Marka
        c.create_oval(22, 18, 48, 44, outline=T.CYAN, width=2)
        c.create_arc(27, 23, 43, 39, start=90, extent=270, style="arc", outline=T.GOLD, width=2)
        c.create_text(60, 25, text="J.A.R.V.I.S", fill=T.TEXT, font=T.display(17), anchor="w")
        c.create_text(61, 45, text="KİŞİSEL ASİSTAN  ·  2.0", fill=T.TEXT_FAINT, font=T.bold(8), anchor="w")
        # Duyulan cümle
        heard = self.s.heard
        text = ("“" + heard[-70:] + "”") if heard else "Konuşmaya başlaman yeterli  ·  F7: cevabı kes ve dinle"
        c.create_text(cx, 28, text=text, fill=T.TEXT_DIM if heard else T.TEXT_FAINT,
                      font=T.body(12) if heard else T.body(11))
        # Sağ: bağlantı + canlı göstergeler + düğmeler
        x = W - 22
        for key, icon in (("settings", "gear"), ("fullscreen", "full")):
            hov = self._hit(x - 30, 14, x, 46, key)
            active = key == "settings" and self._drawer in ("settings", "logs")
            col = T.RED if (key == "power" and hov) else T.CYAN if (hov or active) else T.TEXT_DIM
            if hov or active:
                c.create_rectangle(x - 30, 14, x, 46, outline=T.dim(col, .7), fill=T.dim(col, .12))
            self._icon(icon, x - 15, 30, col, s=9)
            x -= 38
        text, col = self._conn()
        dot = "●" if (self.blink or self.s.cloud) else "○"
        pill_w = 150
        x0 = x - pill_w - 6
        c.create_rectangle(x0, 16, x - 6, 44, outline=T.dim(col, .6), fill=T.dim(col, .10))
        c.create_text(x0 + pill_w / 2, 30, text=f"{dot}  {text}", fill=col, font=T.bold(10))
        chips = []
        if self.s.webcam:
            chips.append("KAMERA CANLI")
        if self.s.screen_watch:
            chips.append("EKRAN İZLENİYOR")
        x1 = x0 - 10
        for chip in chips:                                      # gizlilik: açık olduğu her an görünür
            w = 12 + 7 * len(chip)
            c.create_rectangle(x1 - w, 16, x1, 44, outline=T.RED, fill=T.dim(T.RED, .14))
            c.create_text(x1 - w / 2, 30, text=("● " if self.blink else "◉ ") + chip, fill=T.RED, font=T.bold(9))
            x1 -= w + 8

    def _draw_left(self):
        c = self.c
        x, w = GAP, self.LEFT_W - GAP - GAP // 2
        y = TOP_H + GAP
        # Selam kartı
        h = 74
        self._panel(x, y, w, h)
        c.create_text(x + 18, y + 26, text=_greeting() + ("," if self.name else ""), fill=T.TEXT_DIM,
                      font=T.body(12), anchor="w")
        c.create_text(x + 18, y + 50, text=self.name or "Hoş geldin", fill=T.TEXT, font=T.display(18), anchor="w")
        y += h + GAP
        # Sistem
        st = self.stats
        h = 290
        batt = st["battery"]
        self._panel(x, y, w, h, "SİSTEM", right_text=("⚡ ŞARJDA" if st["charging"] else ""), right_color=T.GOLD)
        r = max(22, min(30, (w - 80) // 8))
        gx = [x + w * 0.27, x + w * 0.73]
        gy = [y + 50 + r + 6, y + 50 + 3 * r + 40]

        def col(p, warn=80, crit=92):
            return T.RED if p >= crit else T.AMBER if p >= warn else T.CYAN
        self._gauge(gx[0], gy[0], r, st["cpu"], col(st["cpu"]), "İŞLEMCİ", f"%{st['cpu']:.0f}")
        self._gauge(gx[1], gy[0], r, st["ram"], col(st["ram"], 85, 95), "BELLEK", f"%{st['ram']:.0f}")
        self._gauge(gx[0], gy[1], r, st["disk"], col(st["disk"], 85, 95), "DİSK", f"%{st['disk']:.0f}")
        if batt is None:
            self._gauge(gx[1], gy[1], r, 100, T.TEAL, "GÜÇ", "AC")
        else:
            self._gauge(gx[1], gy[1], r, batt, T.RED if batt < 15 and not st["charging"] else T.GOLD if batt < 30
                        else T.CYAN, "PİL", f"%{batt:.0f}")
        # Ağ + işlemci grafiği (son ~1 dk)
        sx0, sy1, sw, sh = x + 18, y + h - 14, w - 36, 18

        def rate(v):
            return f"{v / 1024:.1f} MB/s" if v >= 1024 else f"{v:.0f} KB/s"
        c.create_text(x + 18, sy1 - sh - 12, text=f"↓ {rate(st['down'])}    ↑ {rate(st['up'])}", fill=T.TEXT_DIM,
                      font=T.body(9), anchor="w")
        c.create_text(x + w - 18, sy1 - sh - 12, text="İŞLEMCİ · 1 DK", fill=T.TEXT_FAINT, font=T.bold(8), anchor="e")
        c.create_rectangle(sx0, sy1 - sh, sx0 + sw, sy1, outline=T.EDGE, fill=T.dim(T.TEAL, .05))
        pts = []
        for i, v in enumerate(self.cpu_hist):
            pts += [sx0 + sw * i / (len(self.cpu_hist) - 1), sy1 - 1 - (sh - 2) * min(100.0, v) / 100.0]
        if len(pts) >= 4:
            c.create_line(*pts, fill=T.CYAN, width=1)
        y += h + GAP
        # Telefon kartı (en altta) — tıklayınca JARVIS TELEFON paneli açılır
        ph = self.core.phone
        card_h = 66
        cy0 = self.H - FOOT_H - GAP - card_h
        main, sub = ph.summary()
        on = ph.running
        acc = T.CYAN if on and ph.connected else T.GOLD if ph.busy or on else T.TEAL
        hov = self._hit(x, cy0, x + w, cy0 + card_h, "phone")
        self._panel(x, cy0, w, card_h, accent=acc if (hov or on or self._drawer == "phone") else T.dim(T.TEAL, .7))
        px, py = x + 30, cy0 + card_h / 2
        c.create_rectangle(px - 8, py - 14, px + 8, py + 14, outline=acc, width=2)
        c.create_line(px - 3, py + 10, px + 3, py + 10, fill=acc, width=2)
        if on:
            c.create_oval(px + 6, py - 18, px + 14, py - 10, fill=T.CYAN if ph.connected else T.GOLD, outline="")
        c.create_text(x + 52, cy0 + 24, text="TELEFON  ·  " + main, fill=acc if on else T.TEXT, font=T.bold(10),
                      anchor="w")
        c.create_text(x + 52, cy0 + 44, text=(sub or "")[:34], fill=T.TEXT_FAINT, font=T.body(9), anchor="w")
        c.create_text(x + w - 16, py, text="›", fill=T.CYAN if hov else T.TEXT_FAINT, font=T.bold(16), anchor="e")
        # Etkinlik
        h = cy0 - GAP - y
        if h < 110:
            return
        events = list(reversed(self.s.recent_tools()))
        tasks = self.s.visible_tasks()
        running = any(e.ok is None for e in events) or any(
            r.get("status") in ("pending", "running", "waiting_network") for r in tasks)
        self._panel(x, y, w, h, "ETKİNLİK", right_text="ÇALIŞIYOR" if running else "", right_color=T.GOLD)
        row_y = y + 60
        if tasks:
            row_y = self._draw_tasks(x, row_y, w, tasks, y + h - 22)
        if not events and not tasks:
            c.create_text(x + 18, row_y, text="JARVIS'ten bir şey iste; yaptığı her iş\nburada sırayla görünür.",
                          fill=T.TEXT_FAINT, font=T.body(10), anchor="nw", justify="left")
            return
        for ev in events:
            if row_y > y + h - 22:
                break
            if ev.ok is None:
                pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 6)
                dcol, mark = T.mix(T.GOLD, "#fff2b0", pulse), "…"
            elif ev.ok:
                dcol, mark = T.CYAN, "✓"
            else:
                dcol, mark = T.RED, "✕"
            c.create_oval(x + 18, row_y - 4, x + 26, row_y + 4, fill=dcol, outline="")
            c.create_text(x + 36, row_y, text=ev.label[:26], fill=T.TEXT if ev.ok is not False else "#ffb3ba",
                          font=T.body(11), anchor="w")
            c.create_text(x + w - 18, row_y, text=f"{mark}  {_ago(ev.finished or ev.started)}", fill=T.TEXT_FAINT,
                          font=T.body(9), anchor="e")
            row_y += 28

    def _draw_tasks(self, x, row_y, w, rows, bottom):
        """Paralel görev / görev zinciri satırları: durum işareti, ad, yüzde ya da ince ilerleme çubuğu."""
        c = self.c
        c.create_text(x + 18, row_y - 8, text="GÖREVLER", fill=T.TEXT_FAINT, font=T.bold(8), anchor="w")
        row_y += 12
        marks = {"done": (T.CYAN, "✓"), "error": (T.RED, "✕"), "needs_review": (T.AMBER, "?"),
                 "needs_input": (T.AMBER, "?"), "cancelled": (T.TEXT_FAINT, "–"),
                 "waiting_network": (T.AMBER, "…")}
        for r in rows:
            if row_y > bottom - 10:
                break
            status = str(r.get("status", ""))
            if status in ("running", "pending"):
                pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 6)
                dcol, mark = (T.mix(T.GOLD, "#fff2b0", pulse) if status == "running" else T.TEXT_FAINT), "…"
            else:
                dcol, mark = marks.get(status, (T.TEXT_FAINT, "·"))
            pct = r.get("progress_percent")
            right = (f"%{pct}" if isinstance(pct, (int, float)) and status == "running" else
                     {"waiting_network": "ağ bekleniyor", "needs_review": "kontrol", "needs_input": "bilgi gerek",
                      "cancelled": "durdu", "pending": "sırada"}.get(status, mark))
            c.create_rectangle(x + 18, row_y - 4, x + 26, row_y + 4, fill=dcol, outline="")
            c.create_text(x + 36, row_y, text=str(r.get("label", ""))[:24], fill=T.TEXT, font=T.body(10), anchor="w")
            c.create_text(x + w - 18, row_y, text=right, fill=T.TEXT_FAINT, font=T.body(9), anchor="e")
            if isinstance(pct, (int, float)) and status == "running":
                bx0, bx1 = x + 36, x + w - 18
                c.create_line(bx0, row_y + 11, bx1, row_y + 11, fill=T.EDGE, width=2)
                c.create_line(bx0, row_y + 11, bx0 + (bx1 - bx0) * max(0, min(100, pct)) / 100, row_y + 11,
                              fill=T.GOLD, width=2)
            row_y += 26
        c.create_line(x + 18, row_y - 6, x + w - 18, row_y - 6, fill=T.EDGE)
        return row_y + 14

    def _draw_dock(self):
        c, s = self.c, self.s
        bw, bh, gap = 106, 62, 12
        total = len(DOCK) * bw + (len(DOCK) - 1) * gap
        x = self.FCX - total // 2
        y = self.DOCK_Y + (DOCK_H - bh) // 2
        brain_on = self.core.brain.running
        for key, label, sc in DOCK:
            active = {"mic": not s.muted, "pause": s.paused, "cam": s.webcam, "brain": brain_on}.get(key, False)
            hov = self._hit(x, y, x + bw, y + bh, key)
            warn = (key == "mic" and s.muted) or (key == "cam" and active) or (key == "power" and hov)
            base = T.MUTED_RED if (key == "mic" and s.muted) else T.RED if warn else \
                T.dim(T.RED, .75) if key == "power" else \
                T.GOLD if (key == "pause" and s.paused) else T.CYAN if (active and key != "mic") else T.TEAL
            fill = T.dim(base, .18 if (hov or (active and key != "mic") or warn) else .06)
            c.create_rectangle(x, y, x + bw, y + bh, fill=fill, outline=T.dim(base, .9 if hov else .45))
            c.create_line(x, y, x + 14, y, fill=base, width=2)
            c.create_line(x, y, x, y + 10, fill=base, width=2)
            icol = base if (hov or active or warn) else T.TEXT_DIM
            self._icon(key, x + bw / 2, y + 22, icol, s=10,
                       crossed=(key == "mic" and s.muted) or (key == "pause" and s.paused))
            text = {"mic": "KAPALI" if s.muted else "MİKROFON", "pause": "DEVAM" if s.paused else "DURAKLAT"
                    }.get(key, label)
            c.create_text(x + bw / 2, y + 44, text=text, fill=T.TEXT if (hov or active or warn) else T.TEXT_DIM,
                          font=T.bold(8 if len(text) > 10 else 9))
            c.create_text(x + bw - 6, y + 9, text=sc, fill=T.TEXT_FAINT, font=T.body(8), anchor="e")
            x += bw + gap

    def _draw_chat_frame(self):
        c = self.c
        x, y, w, h = self.CHAT_X, self.CHAT_Y, self.CHAT_W, self.CHAT_H
        if self._drawer in ("settings", "logs"):
            # AYARLAR | KAYITLAR sekmeleri (asıl JARVIS'teki ayarlar panelinin "KAYITLAR" sekmesi gibi)
            self._panel(x, y, w, h, accent=T.GOLD)
            tx = x + 18
            for key, label in (("settings", "AYARLAR"), ("logs", "KAYITLAR")):
                active = self._drawer == key
                tw = 92 if key == "settings" else 96
                hov = self._hit(tx - 6, y + 8, tx + tw, y + 36, "tab_" + key)
                c.create_text(tx, y + 22, text=label, font=T.display(11), anchor="w",
                              fill=T.GOLD if active else (T.TEXT if hov else T.TEXT_FAINT))
                if active:
                    c.create_line(tx, y + 37, tx + tw - 14, y + 37, fill=T.GOLD, width=2)
                tx += tw + 12
            c.create_line(x + 18, y + 38, x + w - 18, y + 38, fill=T.EDGE)
            c.create_text(x + w - 18, y + 22, text="KAPAT  ✕", fill=T.TEXT_FAINT, font=T.bold(9), anchor="e")
            self._hit(x + w - 90, y + 8, x + w - 8, y + 36, "close_drawer")
            return
        if self._drawer:
            self._panel(x, y, w, h, "JARVIS TELEFON", accent=T.GOLD, right_text="KAPAT  ✕")
            self._hit(x + w - 90, y + 8, x + w - 8, y + 36, self._drawer)
            return
        self._panel(x, y, w, h, "SOHBET")
        hov = self._hit(x + w - 150, y + 10, x + w - 14, y + 34, "history")
        c.create_text(x + w - 18, y + 22, text="↶  İŞLEM GEÇMİŞİ", fill=T.CYAN if hov else T.TEXT_FAINT,
                      font=T.bold(9), anchor="e")

    def _draw_footer(self):
        c, s, W, H = self.c, self.s, self.W, self.H
        y = H - FOOT_H
        c.create_rectangle(0, y, W, H, fill=T.BG, outline="")
        c.create_line(16, y, W - 16, y, fill=T.EDGE)
        mid = y + FOOT_H // 2
        mode = s.mic_mode
        off = s.muted or "KAPALI" in mode or "HATA" in mode
        dot = T.MUTED_RED if off else T.GOLD if "BEKLEN" in mode or "BAĞLAN" in mode else T.CYAN
        c.create_oval(18, mid - 4, 26, mid + 4, fill=dot, outline="")
        lit_col = T.CYAN if s.mic_level > .025 else T.EDGE_HI
        for k in range(20):
            lit = k / 20.0 < s.mic_level
            c.create_rectangle(36 + k * 5, mid - 4, 39 + k * 5, mid + 4, fill=lit_col if lit else T.EDGE, outline="")
        c.create_text(144, mid, text=f"MİKROFON · {mode}", fill=dot if off else T.TEXT, font=T.bold(9), anchor="w")
        right = "⌘F tam ekran   ⌘, ayarlar   Esc pencere / kapat"
        if s.mic_device:
            right = s.mic_device[:40] + "     " + right
        c.create_text(W - 18, mid, text=right, fill=T.TEXT_FAINT, font=T.body(9), anchor="e")
