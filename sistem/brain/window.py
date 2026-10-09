"""JARVIS İkinci Beyin penceresi (ayrı süreç).

Ana JARVIS arayüzünden ayrı bir süreçte çalışır; büyük grafik çizimi ve el takibi
ana arayüzün 25 FPS döngüsünü yavaşlatmaz. Ana süreçle stdin/stdout üzerinden
satır başına JSON ile konuşur. stdin kapanırsa (ana uygulama kapandı) pencere de
kapanır ve varsa kamera serbest bırakılır.

Çalıştırma: python -m brain.window [--focus "sorgu"] [--ipc]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import random
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import filedialog  # noqa: E402

from brain import BASE_DIR, hand_setup, query, settings  # noqa: E402
from brain.common import is_indexing  # noqa: E402
from brain.gestures import HAND_CONNECTIONS, GestureEngine  # noqa: E402
from brain.hand_actions import HandGraphController  # noqa: E402
from brain.hand_client import TrackerClient  # noqa: E402
from brain.layout import assign_depth  # noqa: E402
from brain.view import GraphView  # noqa: E402

# ── JARVIS HUD paleti (ui.py ile aynı) ──────────────────────────────────────
C_BG, C_PRI, C_ORG, C_ORG2 = "#0a1d23", "#73c1cc", "#d6a84a", "#e0bb5a"
C_MID, C_DIM, C_DIMMER, C_TEXT = "#2f6f7d", "#123540", "#0b2229", "#d7eff4"
C_PANEL, C_GREEN, C_RED, C_BLUE, C_GOLD = "#08181c", "#5fd0f0", "#ff3344", "#8fb6ff", "#d6c052"
FONT_BODY, FONT_DISPLAY = "Grift", "Grift Extra Bold"

TYPE_COLOR = {"note": C_PRI, "file": C_BLUE, "project": C_ORG2, "topic": C_TEXT}   # konu: buz beyazı (nottan ayrılsın)
TYPE_NAME = {"note": "NOT", "file": "DOSYA", "project": "PROJE", "topic": "KONU"}
EDGE_EXPLICIT, EDGE_EXPLICIT_HI = "#2b616f", C_PRI
EDGE_INFERRED, EDGE_INFERRED_HI = "#5f4f24", C_GOLD
# Galaksi görünümü (04.10, HOLO "THE GALAXY" fikri: her not bir yıldız, klasörler takımyıldız, bağlar çizgiler).
# Her klasör (proje) kendi renginde bir takımyıldızdır; düğüm, içinde bulunduğu en yakın klasörün rengini alır.
CLUSTER_PALETTE = ("#5fd0f0", "#ac8eff", "#e0bb5a", "#6fe0b4", "#ff8fa3", "#8fb6ff", "#f0a65f", "#c3e88d",
                   "#7fdbca", "#d7a6ff", "#f3d27a", "#9ad1ff")
TOPIC_STAR = "#d7eff4"         # hiçbir klasöre bağlanamayan konu düğümleri
NEBULA_MIX = (0.975, 0.965, 0.955, 0.945, 0.93, 0.915, 0.9)   # dıştan içe: renk → zemin karışımı
HALO_LIMIT = 220               # en parlak bu kadar yıldıza ışıma halkası (büyük grafikte akıcılık)
MAX_VISIBLE = 1200
EXACT_SYNC_LIMIT = 600
ROTATE_EDGE_LIMIT = 900       # daha çok kenarda fareyle döndürürken kenarlar kısa süre gizlenir (akıcılık)
ROT_PER_PX = 0.008            # fare: piksel başına dönüş (rad) — 400 px ≈ 180°
KEY_ROT = math.radians(12)    # klavye oku başına dönüş
SPIN_STEP = math.radians(0.35)
FOCUS_TYPES = ("project", "topic")   # fareyle çift tık: bu türlerde odaklan (notlar/dosyalar açılır)
FOCUS_ANIM_FRAMES = 10


def mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def fb(size: int, bold: bool = False):
    return (FONT_BODY, size, "bold") if bold else (FONT_BODY, size)


def fd(size: int):
    return (FONT_DISPLAY, size)


def emit(event: dict) -> None:
    """Ana JARVIS sürecine olay bildirir (stdout)."""
    try:
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _hlog(text: str) -> None:
    """El takibi olaylarını günlüğe yazar (stderr → memory/second_brain.log). Görüntü yazılmaz."""
    try:
        sys.stderr.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] el: {text}\n")
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass


class HudButton(tk.Canvas):
    """ui.py'deki köşe braketli düğme stili."""

    def __init__(self, parent, text: str, command, width: int = 150, height: int = 30,
                 color: str = C_PRI, bg: str = C_BG):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, cursor="hand2")
        self.text, self.command, self.color, self.enabled = text, command, color, True
        self._hover = False
        self.bind("<Button-1>", lambda e: self.enabled and self.command and self.command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, value: bool):
        self._hover = value
        self.draw()

    def configure_button(self, text: str | None = None, color: str | None = None, enabled: bool | None = None):
        if text is not None:
            self.text = text
        if color is not None:
            self.color = color
        if enabled is not None:
            self.enabled = enabled
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        col = self.color if self.enabled else C_DIM
        if self._hover and self.enabled:
            self.create_rectangle(0, 0, w, h, fill="#0c252b", outline="")
        bl = 7
        for bx, by, sx, sy in [(0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)]:
            self.create_line(bx, by, bx + sx * bl, by, fill=col, width=2)
            self.create_line(bx, by, bx, by + sy * bl, fill=col, width=2)
        self.create_text(w // 2, h // 2, text=self.text, fill=col, font=fb(10, True))


class HudToggle(tk.Canvas):
    def __init__(self, parent, text: str, color: str, value: bool, command, width: int = 222,
                 style: str = "dot"):
        super().__init__(parent, width=width, height=24, bg=C_PANEL, highlightthickness=0, cursor="hand2")
        self.text, self.color, self.value, self.command, self.style = text, color, value, command, style
        self.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _e=None):
        self.value = not self.value
        self.draw()
        self.command(self.value)

    def set_text(self, text: str):
        self.text = text
        self.draw()

    def draw(self):
        self.delete("all")
        col = self.color if self.value else "#364c52"
        if self.style == "line":
            self.create_line(6, 12, 30, 12, fill=col, width=2)
        elif self.style == "dash":
            self.create_line(6, 12, 30, 12, fill=col, width=2, dash=(4, 3))
        else:
            self.create_oval(10, 6, 22, 18, fill=col if self.value else "", outline=col, width=2)
        self.create_text(40, 12, text=self.text, anchor="w", fill=C_TEXT if self.value else "#546a6f",
                         font=fb(10))


class BrainApp:
    def __init__(self, root: tk.Tk, focus: str = "", ipc: bool = False):
        self.root = root
        self.ipc = ipc
        self.focus_request = focus
        self.graph = None
        self.nodes: dict = {}
        self.view = GraphView({}, 800, 600)
        self.selected: str | None = None
        self.focus_ids: set[str] = set()
        self.search_hits: set[str] = set()
        self.neighbor_mode = False
        self.type_on = {"note": True, "file": True, "project": True, "topic": True}
        self.show_explicit = True
        self.show_inferred = True
        self.min_conf = 0.0
        self.source_filter = ""
        self.node_items: dict[str, int] = {}
        self.label_items: dict[str, int] = {}
        self.edge_items: list[tuple[int, dict]] = []
        self.node_edges: dict[str, list[int]] = {}
        self.drag: dict | None = None
        self._settle_job = None
        self._search_job = None
        self._index_proc = None
        self._frame_times: list[float] = []
        self.render_fps = 0.0
        self.cmd_queue: "queue.Queue[dict]" = queue.Queue()
        # el kontrolü
        self.engine = GestureEngine()
        self.hand_ctrl = HandGraphController(self.view)
        self.tracker = TrackerClient()
        self.hand_enabled = settings.hand_control_enabled()
        self.hand_state = "off"   # off | checking | installing | starting | active | paused | error
        self.hand_message = ""
        self.hand_setup_status = ""
        self.hand_last_seen = time.monotonic()
        self.hand_last_frame = 0.0
        self.hand_stats = {}
        self._hand_job = None
        self._unmap_paused = False          # kamera pencere gizlendiği için mi duraklatıldı
        self.hand_camera_active = None              # takip sürecinin kullandığı kamera (yalnızca Mac'in kendisi)
        self.hand_camera_name = ""
        self._hand_frames = [0, 0]          # [kare, eli görülen kare] (günlük için)
        self._hand_log_at = time.monotonic()
        self.hud_items: dict = {}
        self.hover_id: str | None = None
        self.hand_quality: tuple = ("", [])
        self._fitted = False
        self._rank: list[str] = []
        self._project_ids: set[str] = set()
        # 3B görünüm
        self.mode3d = settings.view_3d_enabled()
        self.spin = False
        self._spin_job = None
        self._frame_job = None
        self._depth_job = None
        self._spin_shade_at = 0.0
        self.edges_hidden = False
        self._anim_job = None

        self._build()
        self.load_graph(initial=True)
        self.root.after(250, self._poll_commands)
        self.root.after(1200, self._maybe_reindex)
        if self.hand_enabled:
            self.root.after(600, self.start_hands)
        if ipc:
            threading.Thread(target=self._stdin_reader, daemon=True).start()
        emit({"event": "ready", "pid": os.getpid()})

    # ── arayüz iskeleti ──────────────────────────────────────────────────
    def _build(self):
        r = self.root
        r.configure(bg=C_BG)
        header = tk.Canvas(r, height=58, bg="#07161a", highlightthickness=0)
        header.pack(side="top", fill="x")
        self.header = header
        header.bind("<Configure>", lambda e: self._draw_header())

        btns = tk.Frame(header, bg="#07161a")
        self.btn_close = HudButton(btns, "✕  KAPAT", self.close, width=96, color=C_RED, bg="#07161a")
        self.btn_hand = HudButton(btns, "✋ EL KONTROLÜ: KAPALI", self.toggle_hands, width=210, color=C_MID,
                                  bg="#07161a")
        self.btn_camera = HudButton(btns, self._camera_button_text(), self.show_camera_info, width=150,
                                    color=C_MID, bg="#07161a")
        self.btn_reset = HudButton(btns, "⟲  SIFIRLA", self.reset_view, width=110, bg="#07161a")
        self.btn_reindex = HudButton(btns, "↻  YENİDEN TARA", self.reindex, width=150, bg="#07161a")
        self.btn_sources = HudButton(btns, "+  KAYNAK EKLE", self.add_source, width=140, color=C_BLUE,
                                     bg="#07161a")
        self.simple = True   # basit görünüm: az düğme, filtreler "Gelişmiş" altında (kullanıcı isteği)
        self._pack_header_buttons()
        self.header_btns = header.create_window(0, 0, window=btns, anchor="ne")

        self.footer = tk.Label(r, text="", anchor="w", bg="#07161a", fg=C_MID, font=fb(9), padx=12)
        self.footer.pack(side="bottom", fill="x")  # gövdeden önce: pencere küçülünce kaybolmasın
        body = tk.Frame(r, bg=C_BG)
        body.pack(side="top", fill="both", expand=True)
        self.left = tk.Frame(body, bg=C_PANEL, width=236, highlightthickness=1, highlightbackground=C_DIM)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)
        self.right = tk.Frame(body, bg=C_PANEL, width=324, highlightthickness=1, highlightbackground=C_DIM)
        self.right.pack(side="right", fill="y")
        self.right.pack_propagate(False)
        self.canvas = tk.Canvas(body, bg=C_BG, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True)

        self._build_left()
        self._build_right()
        self._bind_events()

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=C_PANEL, fg=C_MID, font=fb(9, True), anchor="w").pack(
            fill="x", padx=14, pady=(14, 4))

    def _pack_header_buttons(self):
        """Basit görünümde yalnız sık kullanılanlar; kamera bilgisi ve yeniden tarama Gelişmiş'te."""
        order = [self.btn_close, self.btn_hand, self.btn_camera, self.btn_reset, self.btn_reindex, self.btn_sources]
        for b in order:
            b.pack_forget()
        for b in order:
            if self.simple and b in (self.btn_camera, self.btn_reindex):
                continue
            b.pack(side="right", padx=5, pady=13)

    def toggle_advanced(self, event=None):
        self.simple = not self.simple
        if self.simple:
            self.adv_frame.pack_forget()
            self.guide_title.pack(fill="x", padx=14, pady=(14, 4), before=self.adv_button)
            self.guide_label.pack(fill="x", padx=14, pady=(0, 6), before=self.adv_button)
        else:
            self.guide_title.pack_forget()
            self.guide_label.pack_forget()
            self.adv_frame.pack(fill="x", before=self.adv_anchor)
        self.adv_button.configure(text="⚙  GELİŞMİŞ  ▸" if self.simple else "⚙  GELİŞMİŞ  ▾")
        self._pack_header_buttons()

    def _build_left(self):
        L = self.left
        self._section(L, "ARA")
        self.search_var = tk.StringVar()
        self.search = tk.Entry(L, textvariable=self.search_var, bg="#091c20", fg=C_TEXT, insertbackground=C_TEXT,
                               relief="flat", highlightthickness=1, highlightbackground=C_DIM,
                               highlightcolor=C_PRI, font=fb(11))
        self.search.pack(fill="x", padx=14, ipady=5)
        self.search_info = tk.Label(L, text="Ad, etiket veya metin · Enter: git", bg=C_PANEL, fg="#56747b",
                                    font=fb(8), anchor="w")
        self.search_info.pack(fill="x", padx=14, pady=(3, 0))

        # Kısa kullanım kartı: ilk bakışta ne yapılacağı belli olsun (Gelişmiş açıkken yer açmak için gizlenir).
        self.guide_title = tk.Label(L, text="NASIL KULLANILIR", bg=C_PANEL, fg=C_MID, font=fb(9, True), anchor="w")
        self.guide_title.pack(fill="x", padx=14, pady=(14, 4))
        guide = ("1  Aramak için yukarıya yaz, Enter'a bas.\n"
                 "2  Bir noktaya tıkla: sağda ayrıntısı çıkar.\n"
                 "3  Nota çift tıkla: dosya açılır.\n"
                 "4  JARVIS'e sor: “bunun içinde ne var?”\n\n"
                 "Sürükle: döndür · Tekerlek: yakınlaştır\n0, ⟲ SIFIRLA ya da ✌ (1 sn): başa dön")
        self.guide_label = tk.Label(L, text=guide, justify="left", bg=C_PANEL, fg=C_TEXT, font=fb(10), anchor="w",
                                    wraplength=208)
        self.guide_label.pack(fill="x", padx=14, pady=(0, 6))

        self.adv_button = tk.Label(L, text="⚙  GELİŞMİŞ  ▸", bg="#0c252b", fg=C_PRI, font=fb(9, True),
                                   cursor="hand2", anchor="w", padx=10, pady=6,
                                   highlightthickness=1, highlightbackground=C_DIM)
        self.adv_button.pack(fill="x", padx=14, pady=(10, 0))
        self.adv_button.bind("<Button-1>", self.toggle_advanced)
        A = self.adv_frame = tk.Frame(L, bg=C_PANEL)        # basit görünümde gizli
        self.adv_anchor = tk.Frame(L, bg=C_PANEL, height=1)  # Gelişmiş bölümü bunun üstüne açılır
        self.adv_anchor.pack(fill="x")

        self._section(A, "GÖRÜNÜM")
        self.tg_3d = HudToggle(A, "3B görünüm", C_PRI, self.mode3d, self._set_mode3d)
        self.tg_3d.pack(anchor="w", padx=10)
        self.tg_spin = HudToggle(A, "Otomatik döndür", C_GOLD, False, self._set_spin)
        self.tg_spin.pack(anchor="w", padx=10)

        self._section(A, "TÜRLER")
        self.type_toggles = {}
        for t in ("note", "file", "project", "topic"):
            tg = HudToggle(A, TYPE_NAME[t].title(), TYPE_COLOR[t], True,
                           lambda v, t=t: self._set_type(t, v))
            tg.pack(anchor="w", padx=10)
            self.type_toggles[t] = tg

        self._section(A, "İLİŞKİLER")
        self.tg_explicit = HudToggle(A, "Açık bağlantılar", EDGE_EXPLICIT_HI, True, self._set_explicit, style="line")
        self.tg_explicit.pack(anchor="w", padx=10)
        self.tg_inferred = HudToggle(A, "Tahmini ilişkiler", EDGE_INFERRED_HI, True, self._set_inferred, style="dash")
        self.tg_inferred.pack(anchor="w", padx=10)
        self.conf_label = tk.Label(A, text="Tahmin için en düşük güven: %0", bg=C_PANEL, fg=C_TEXT,
                                   font=fb(9), anchor="w")
        self.conf_label.pack(fill="x", padx=14, pady=(8, 0))
        self.conf_scale = tk.Scale(A, from_=0, to=90, orient="horizontal", showvalue=False, resolution=5, length=200,
                                   troughcolor="#0b2025", bg=C_PANEL, fg=C_TEXT, activebackground=C_PRI,
                                   highlightthickness=0, borderwidth=0, sliderlength=16, width=9,
                                   command=self._set_conf)
        self.conf_scale.pack(fill="x", padx=14)

        self._section(A, "KAYNAK")
        self.source_var = tk.StringVar(value="Tüm kaynaklar")
        self.source_menu = tk.OptionMenu(A, self.source_var, "Tüm kaynaklar")
        self.source_menu.config(fg=C_PRI, bg=C_PANEL, activeforeground=C_BG, activebackground=C_PRI,
                                font=fb(10), borderwidth=0, highlightthickness=1, highlightbackground=C_MID)
        self.source_menu["menu"].config(fg=C_PRI, bg=C_PANEL, font=fb(10), activeforeground=C_BG,
                                        activebackground=C_PRI)
        self.source_menu.pack(fill="x", padx=14)

        self._section(A, "TÜM KISAYOLLAR")
        help_text = ("━ açık bağlantı · ┅ tahmini ilişki\n○ içi boş konu: tahmini anahtar terim\n\n"
                     "Fare: boşlukta sürükle = döndür (3B)\n⇧ ya da sağ tuşla sürükle = kaydır\n"
                     "düğümü sürükle = taşı\nçift tık: sarı proje/konu = odak, not = aç\n"
                     "Klavye: oklar döndür, ⇧+oklar kaydır\n+ − yakınlaştır · 3 = 3B/2B · D döndür\n"
                     "0 sıfırla · ⌘F ara · ⌘O aç · H el\n\n"
                     "El: parmakları birleştir = tut/taşı\nboşlukta birleştirip sürükle = döndür\n"
                     "iki kez hızlı birleştir = odak · Esc: çık\niki elle = yakınlaştır · ✌ 1 sn tut = sıfırla\n"
                     "yumruk yapıp gezdir = dokunmadan kaydır\n"
                     "tek elle iki kez hızlı yumruk = zoomdan çık")
        tk.Label(A, text=help_text, justify="left", bg=C_PANEL, fg="#699ba7", font=fb(9), anchor="w",
                 wraplength=208).pack(fill="x", padx=14, pady=(0, 8))

    def _build_right(self):
        R = self.right
        tk.Label(R, text="AYRINTI", bg=C_PANEL, fg=C_PRI, font=fd(11), anchor="w").pack(fill="x", padx=14,
                                                                                    pady=(14, 6))
        self.details = tk.Text(R, bg="#07161a", fg=C_TEXT, wrap="word", relief="flat", highlightthickness=1,
                               highlightbackground=C_DIM, font=fb(10), padx=10, pady=10, cursor="arrow")
        self.details.pack(fill="both", expand=True, padx=12)
        d = self.details
        d.tag_config("title", foreground=C_TEXT, font=fd(13))
        d.tag_config("badge", foreground=C_BG, font=fb(9, True))
        d.tag_config("muted", foreground="#699ba7", font=fb(9))
        d.tag_config("head_exp", foreground=C_PRI, font=fb(10, True), spacing1=10)
        d.tag_config("head_inf", foreground=C_GOLD, font=fb(10, True), spacing1=10)
        d.tag_config("rel", foreground=C_TEXT, font=fb(10, True), spacing1=6)
        d.tag_config("reason", foreground="#a8cfd9", font=fb(9), lmargin1=14, lmargin2=14)
        d.tag_config("evidence", foreground="#76969e", font=fb(9), lmargin1=14, lmargin2=14)
        d.tag_config("warn", foreground=C_GOLD, font=fb(9))
        for t, col in TYPE_COLOR.items():
            d.tag_config("badge_" + t, background=col, foreground=C_BG, font=fb(9, True))
        d.configure(state="disabled")
        row = tk.Frame(R, bg=C_PANEL)
        row.pack(fill="x", padx=12, pady=10)
        self.btn_open = HudButton(row, "DOSYAYI AÇ", self.open_selected, width=104, bg=C_PANEL)
        self.btn_reveal = HudButton(row, "FINDER'DA", self.reveal_selected, width=96, bg=C_PANEL)
        self.btn_neigh = HudButton(row, "KOMŞULAR", self.toggle_neighbors, width=96, color=C_GOLD, bg=C_PANEL)
        for b in (self.btn_open, self.btn_reveal, self.btn_neigh):
            b.pack(side="left", padx=(0, 6))
        self._show_details(None)

    def _bind_events(self):
        c = self.canvas
        c.bind("<Configure>", self._on_resize)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self._zoom_center(1.15, e.x, e.y))
        c.bind("<Button-5>", lambda e: self._zoom_center(1 / 1.15, e.x, e.y))
        c.bind("<Motion>", self._on_motion)
        for b in ("2", "3"):   # sağ tuş (macOS'ta 2) ile sürükle = kaydır
            c.bind(f"<ButtonPress-{b}>", self._on_pan_press)
            c.bind(f"<B{b}-Motion>", self._on_drag)
            c.bind(f"<ButtonRelease-{b}>", self._on_release)
        r = self.root
        r.bind("<KeyPress>", self._on_key)
        r.bind("<Command-f>", lambda e: self._focus_search())
        r.bind("<Command-o>", lambda e: self.open_selected())
        r.bind("<Command-w>", lambda e: self.close())
        r.bind("<Unmap>", self._on_unmap)
        r.bind("<Map>", self._on_map)
        r.protocol("WM_DELETE_WINDOW", self.close)
        self.search.bind("<KeyRelease>", self._on_search_key)
        self.search.bind("<Return>", self._on_search_enter)
        self.search.bind("<Escape>", lambda e: self._clear_search())

    # ── veri ─────────────────────────────────────────────────────────────
    def load_graph(self, initial: bool = False):
        keep_view = (not initial) and self.nodes
        v = self.view
        old = (v.scale, v.ox, v.oy, v.yaw, v.pitch, v.pivot, v.cam_dist, v.extent)
        self.graph = query.load(settings.DB_PATH, settings.get_sources())
        self.nodes = self.graph["nodes"] if self.graph else {}
        positions = {nid: (n["x"] or 0.0, n["y"] or 0.0) for nid, n in self.nodes.items()}
        depths = assign_depth(positions, self.graph["edges"] if self.graph else [],
                              {nid: n["type"] for nid, n in self.nodes.items()})
        w = max(200, self.canvas.winfo_width())
        h = max(200, self.canvas.winfo_height())
        self.view = GraphView(positions, w, h, depths=depths)
        self.view.set_mode3d(self.mode3d)
        self.hand_ctrl.view = self.view
        for nid, n in self.nodes.items():
            base = {"project": 11.0, "topic": 7.5, "note": 6.5, "file": 5.0}.get(n["type"], 5.0)
            self.view.radius[nid] = min(22.0, base + math.sqrt(n.get("degree") or 0) * 0.9)
        self._compute_clusters()
        self._refresh_source_menu()
        self._apply_filters(redraw=False)
        if keep_view:
            v = self.view
            v.scale, v.ox, v.oy = old[:3]
            if v.mode3d:
                v.yaw, v.pitch, v.pivot, v.cam_dist, v.extent = old[3:]
                v.invalidate()
        else:
            self.view.fit()
        if self.selected and self.selected not in self.nodes:
            self.selected = None
        self.redraw()
        self._show_details(self.selected)
        self._update_footer()
        if initial and self.focus_request:
            self.root.after(150, lambda: self.focus_query(self.focus_request))

    def _importance_order(self) -> list[str]:
        prio = {"project": 0, "topic": 1, "note": 2, "file": 3}
        return sorted(self.nodes, key=lambda i: (prio.get(self.nodes[i]["type"], 9),
                                                 -(self.nodes[i].get("degree") or 0)))

    def _apply_filters(self, redraw: bool = True):
        cands = []
        for nid in self._importance_order():
            n = self.nodes[nid]
            if not self.type_on.get(n["type"], True):
                continue
            if self.source_filter and n.get("root") and n["root"] != self.source_filter:
                continue
            cands.append(nid)
        visible = set(cands[:MAX_VISIBLE])
        visible |= {i for i in self.search_hits if i in self.nodes}
        if self.neighbor_mode and self.selected in self.nodes:
            neigh = {self.selected} | self._neighbors(self.selected)
            visible = neigh
        self.hidden_by_cap = max(0, len(cands) - MAX_VISIBLE)
        self.view.set_visible(visible)
        if redraw:
            self.redraw()
            self._update_footer()
        self._report_selection()

    def _edge_visible(self, e: dict) -> bool:
        if e["kind"] == "explicit" and not self.show_explicit:
            return False
        if e["kind"] == "inferred":
            if not self.show_inferred or (e.get("confidence") or 0) * 100 < self.min_conf:
                return False
        return e["src"] in self.view.visible and e["dst"] in self.view.visible

    def _neighbors(self, nid: str) -> set[str]:
        out = set()
        if not self.graph:
            return out
        for e in self.graph["adj"].get(nid, []):
            out.add(e["dst"] if e["src"] == nid else e["src"])
        return out

    # ── galaksi: takımyıldızlar ───────────────────────────────────────────
    def _compute_clusters(self):
        """Her düğüme takımyıldız (en yakın kapsayan klasör) ve renk atar. Konular, bağlı oldukları
        düğümlerin çoğunluğunun takımyıldızına katılır."""
        projects = sorted((n for n in self.nodes.values() if n["type"] == "project" and n.get("path")),
                          key=lambda n: (n["path"].count("/"), n["path"]))
        self.cluster_color = {}
        for i, n in enumerate(projects):
            self.cluster_color[n["id"]] = CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)]
        deep = sorted(projects, key=lambda n: -len(n["path"]))
        self.cluster_of = {}
        for nid, n in self.nodes.items():
            path = n.get("path") or ""
            if n["type"] == "project" and nid in self.cluster_color:
                self.cluster_of[nid] = nid
                continue
            for pr in deep:
                if path and (path == pr["path"] or path.startswith(pr["path"].rstrip("/") + "/")):
                    self.cluster_of[nid] = pr["id"]
                    break
        adj = self.graph["adj"] if self.graph else {}
        for nid, n in self.nodes.items():
            if nid in self.cluster_of:
                continue
            votes: dict[str, int] = {}
            for e in adj.get(nid, []):
                other = e["dst"] if e["src"] == nid else e["src"]
                k = self.cluster_of.get(other)
                if k:
                    votes[k] = votes.get(k, 0) + 1
            if votes:
                self.cluster_of[nid] = max(votes, key=votes.get)

    def _star_color(self, nid: str) -> str:
        k = getattr(self, "cluster_of", {}).get(nid)
        return self.cluster_color.get(k, TOPIC_STAR) if k else TOPIC_STAR

    def _draw_starfield(self):
        """Arka planda sabit, soluk yıldız alanı (grafikten bağımsız; pencere boyutu değişince yeniden)."""
        c = self.canvas
        c.delete("stars")
        w, h = max(300, c.winfo_width()), max(200, c.winfo_height())
        rnd = random.Random(7)
        for _ in range(int(w * h / 5200)):
            x, y = rnd.uniform(0, w), rnd.uniform(0, h)
            r = rnd.choice((0.6, 0.8, 0.8, 1.0, 1.3))
            col = mix(rnd.choice(("#d7eff4", "#9ad1ff", "#73c1cc", "#e0d6b4")), C_BG, rnd.uniform(0.55, 0.85))
            c.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="", tags=("stars",))
        c.tag_lower("stars")

    # ── çizim ────────────────────────────────────────────────────────────
    def _node_colors(self, nid: str, lit: bool | None) -> tuple[str, str]:
        n = self.nodes[nid]
        col = self._star_color(nid)
        hollow = n["type"] == "topic" and n["meta"].get("origin") == "keyword"
        if lit is False:
            col = mix(col, C_BG, 0.78)
        elif lit is None:
            col = mix(col, C_BG, 0.25)
        return ("" if hollow else col), col

    def redraw(self):
        c = self.canvas
        c.delete("world")
        self.edges_hidden = False
        self.node_items.clear()
        self.label_items.clear()
        self.edge_items.clear()
        self.node_edges = {}
        if not self.nodes:
            self._draw_empty()
            return
        c.delete("empty")
        self.halo_items = {}
        self.nebula_items = {}
        self._draw_starfield()
        # Bulutsu: her görünen klasörün etrafında kendi renginde hafif ışık bulutu (kenarlardan önce, en altta)
        for nid in self.view.visible:
            if self.nodes[nid]["type"] != "project":
                continue
            x, y = self.view.node_screen(nid)
            r = self.view.screen_radius(nid)
            col = self._star_color(nid)
            ids = []
            # Tk'de saydamlık yok: iç içe, merkeze doğru biraz daha parlak 7 disk = yumuşak ışık bulutu
            ks = (7.0, 6.0, 5.1, 4.3, 3.6, 3.0, 2.5)
            for k, amt in zip(ks, NEBULA_MIX):
                ids.append(c.create_oval(x - r * k, y - r * k, x + r * k, y + r * k, fill=mix(col, C_BG, amt),
                                         outline="", tags=("world", "nebula")))
            self.nebula_items[nid] = (ids, ks)
        for e in (self.graph["edges"] if self.graph else []):
            if not self._edge_visible(e):
                continue
            x1, y1 = self.view.node_screen(e["src"])
            x2, y2 = self.view.node_screen(e["dst"])
            inferred = e["kind"] == "inferred"
            item = c.create_line(x1, y1, x2, y2, fill=EDGE_INFERRED if inferred else EDGE_EXPLICIT,
                                 width=1, dash=(3, 4) if inferred else (),
                                 tags=("world", "edge", "e_inf" if inferred else "e_exp"))
            idx = len(self.edge_items)
            self.edge_items.append((item, e))
            self.node_edges.setdefault(e["src"], []).append(idx)
            self.node_edges.setdefault(e["dst"], []).append(idx)
        bright_ids = set(sorted(self.view.visible, key=lambda i: -(self.nodes[i].get("degree") or 0))[:HALO_LIMIT])
        for nid in self.view.visible:
            x, y = self.view.node_screen(nid)
            r = self.view.screen_radius(nid)
            fill, outline = self._node_colors(nid, None)
            t = self.nodes[nid]["type"]
            if fill and (t == "project" or nid in bright_ids):      # yıldız ışıması
                hr = r * (2.3 if t == "project" else 1.9)
                self.halo_items[nid] = c.create_oval(x - hr, y - hr, x + hr, y + hr,
                                                     fill=mix(self._star_color(nid), C_BG, 0.78), outline="",
                                                     tags=("world", "halo"))
            tags = ("world", "node", "n_" + t, "cl_" + self._cluster_tag(nid)) + (("n_kw",) if not fill else ())
            self.node_items[nid] = c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline,
                                                 width=2 if not fill else 1, tags=tags)
            label = self.nodes[nid]["label"]
            self.label_items[nid] = c.create_text(
                x + r + 4, y, text=label if len(label) <= 34 else label[:33] + "…", anchor="w",
                fill=C_TEXT if t != "project" else mix(self._star_color(nid), "#ffffff", 0.35),
                font=fb(11 if t == "project" else 9, t == "project"),
                tags=("world", "label"), state="hidden")
        c.tag_lower("nebula")
        c.tag_lower("stars")
        c.tag_raise("halo")
        c.tag_raise("node")
        c.tag_raise("label")
        c.tag_raise("hud")
        self._rank = sorted(self.view.visible, key=lambda i: -(self.nodes[i].get("degree") or 0))
        self._project_ids = {i for i in self.view.visible if self.nodes[i]["type"] == "project"}
        self._set_antialias(len(self.edge_items) <= 600)
        self._apply_highlight()
        self._update_labels()

    def _cluster_tag(self, nid: str) -> str:
        k = getattr(self, "cluster_of", {}).get(nid)
        return str(sorted(self.cluster_color).index(k)) if k in self.cluster_color else "none"

    def _draw_empty(self):
        c = self.canvas
        c.delete("empty")
        w, h = max(300, c.winfo_width()), max(200, c.winfo_height())
        st = query.status(settings.DB_PATH)
        action = None
        if not st["sources"]:
            msg = ("HENÜZ KLASÖR SEÇİLMEDİ\n\nİkinci Beyin, senin seçtiğin not ve proje klasörlerinden\n"
                   "bir bilgi haritası çıkarır. Başlamak için bir klasör seç.")
            action = ("＋  KLASÖR EKLE", self.add_source)
        elif st["indexing"] or self._index_proc is not None:
            msg = "TARANIYOR…\n\nSeçili klasörler indeksleniyor; bitince grafik burada belirir."
        else:
            msg = "DİZİN BOŞ\n\nSeçili klasörlerde dosya bulunamadı ya da tarama henüz yapılmadı."
            action = ("↻  YENİDEN TARA", self.reindex)
        c.create_text(w / 2, h / 2 - 30, text=msg, fill=C_MID, font=fb(13), justify="center", tags=("empty",))
        if action is not None:
            # Büyük, tıklanabilir düğme: üstteki küçük düğmeyi aramak gerekmesin.
            bw, bh, by = 240, 48, h / 2 + 70
            c.create_rectangle(w / 2 - bw / 2, by - bh / 2, w / 2 + bw / 2, by + bh / 2, fill="#0c252b",
                               outline=C_PRI, width=2, tags=("empty", "empty_btn"))
            c.create_text(w / 2, by, text=action[0], fill=C_PRI, font=fb(14, True), tags=("empty", "empty_btn"))
            c.tag_bind("empty_btn", "<Button-1>", lambda e, run=action[1]: run())
            c.tag_bind("empty_btn", "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind("empty_btn", "<Leave>", lambda e: c.configure(cursor="crosshair"))

    def _update_labels(self):
        """Etiket ayrıntı düzeyi + çakışma önleme (04.10): önce seçili ve odaktakiler, sonra klasörler, sonra en
        bağlantılılar; başka bir etiketin üstüne binecek etiket gizlenir (üst üste okunmaz yazı olmasın)."""
        c = self.canvas
        if self.view.scale >= 0.9 and len(self.view.visible) <= 500:
            cand = list(self.label_items)
        else:
            cand = list(set(self._rank[:max(8, int(25 * self.view.scale))]) | self._project_ids | self.focus_ids)
        c.itemconfigure("label", state="hidden")

        def prio(nid):
            deg = self.nodes[nid].get("degree") or 0
            return (nid != self.selected, nid not in self.focus_ids, self.nodes[nid]["type"] != "project", -deg)
        cand.sort(key=prio)
        cell = 60
        grid: dict[tuple[int, int], list] = {}
        for nid in cand[:400]:
            item = self.label_items.get(nid)
            if item is None:
                continue
            c.itemconfigure(item, state="normal")
            bb = c.bbox(item)
            if not bb:
                continue
            x0, y0, x1, y1 = bb[0] - 2, bb[1], bb[2] + 2, bb[3]
            keys = [(gx, gy) for gx in range(int(x0 // cell), int(x1 // cell) + 1)
                    for gy in range(int(y0 // cell), int(y1 // cell) + 1)]
            hit = any(not (x1 < a or x0 > b or y1 < cc or y0 > d) for k in keys for (a, cc, b, d) in grid.get(k, ()))
            if hit and nid != self.selected:
                c.itemconfigure(item, state="hidden")
                continue
            for k in keys:
                grid.setdefault(k, []).append((x0, y0, x1, y1))

    def _apply_highlight(self):
        """Seçim/arama varsa ilgili düğüm ve bağlantıları parlat, diğerlerini soldur.

        Önce tür etiketleriyle toplu renklendirme (birkaç Tk çağrısı), sonra yalnızca odaktaki
        öğeler tek tek — binlerce öğede bile seçim anlık kalır.
        """
        c = self.canvas
        focus = set(self.search_hits)
        if self.selected and self.selected in self.nodes:
            focus |= {self.selected} | self._neighbors(self.selected)
        self.focus_ids = {i for i in focus if i in self.node_items}
        active = bool(self.focus_ids)
        for nid, (ids, _ks) in getattr(self, "nebula_items", {}).items():   # odak dışındaki bulutsular söner
            fade = 0.75 if active and nid not in self.focus_ids else 0.0
            col = self._star_color(nid)
            for item, amt in zip(ids, NEBULA_MIX):
                c.itemconfigure(item, fill=mix(col, C_BG, amt + (1 - amt) * fade))
        if self.view.mode3d:
            self._shade_depth(active)
            self._order_depth()
        else:
            amount = 0.78 if active else 0.12
            for i, k in enumerate(sorted(self.cluster_color)):
                base = mix(self.cluster_color[k], C_BG, amount)
                c.itemconfigure("cl_" + str(i), fill=base, outline=mix(base, "#ffffff", 0.25), width=1)
            base = mix(TOPIC_STAR, C_BG, amount)
            c.itemconfigure("cl_none", fill=base, outline=base, width=1)
            c.itemconfigure("n_kw", fill="", width=2)
            for nid, item in self.halo_items.items():
                c.itemconfigure(item, fill=mix(self._star_color(nid), C_BG, 0.93 if active else 0.78))
            for nid in self.focus_ids:
                fill, outline = self._node_colors(nid, True)
                c.itemconfigure(self.node_items[nid], fill=fill, outline=outline, width=2 if not fill else 1)
        dim = 0.7 if active else 0.0
        c.itemconfigure("e_exp", fill=mix(EDGE_EXPLICIT, C_BG, dim), width=1)
        c.itemconfigure("e_inf", fill=mix(EDGE_INFERRED, C_BG, dim), width=1)
        if self.selected in self.node_items:
            c.itemconfigure(self.node_items[self.selected], outline=C_TEXT, width=3)
            for idx in self.node_edges.get(self.selected, ()):
                item, e = self.edge_items[idx]
                c.itemconfigure(item, fill=EDGE_INFERRED_HI if e["kind"] == "inferred" else EDGE_EXPLICIT_HI,
                                width=2)
                c.tag_raise(item)
            c.tag_raise("node")
            c.tag_raise("label")
            c.tag_raise(self.node_items[self.selected])
            c.tag_raise("hud")

    # ── 3B: derinlik gölgesi, çizim sırası, döndürme ─────────────────────
    def _shade_depth(self, active: bool):
        """Uzaktaki düğümler arka plana doğru solar; yakındakiler parlak. Odak varsa diğerleri soluk."""
        c = self.canvas
        halos = getattr(self, "halo_items", {})
        for nid, item in self.node_items.items():
            n = self.nodes[nid]
            col = self._star_color(nid)
            hollow = n["type"] == "topic" and n["meta"].get("origin") == "keyword"
            d = self.view.depth01(nid)
            if nid in self.focus_ids:
                amount = 0.28 * d
            elif active:
                amount = 0.74 + 0.14 * d
            else:
                amount = 0.08 + 0.55 * d
            shade = mix(col, C_BG, min(0.9, amount))
            c.itemconfigure(item, fill="" if hollow else shade, outline=shade, width=2 if hollow else 1)
            h = halos.get(nid)
            if h is not None:
                c.itemconfigure(h, fill=mix(col, C_BG, min(0.96, 0.78 + 0.18 * min(1.0, amount / 0.6))))

    def _order_depth(self):
        """Arkadaki düğümler önce, öndekiler üstte çizilsin (yalnızca döndürme durunca)."""
        c = self.canvas
        halos = getattr(self, "halo_items", {})
        for nid in sorted(self.node_items, key=lambda i: -self.view.project(i)[3]):
            if nid in halos:
                c.tag_raise(halos[nid])
            c.tag_raise(self.node_items[nid])
        c.tag_lower("nebula")
        c.tag_lower("stars")
        c.tag_raise("label")
        c.tag_raise("hover")
        c.tag_raise("gizmo")
        c.tag_raise("hud")

    def _set_mode3d(self, on: bool, persist: bool = True):
        on = bool(on)
        self.mode3d = on
        if self.tg_3d.value != on:
            self.tg_3d.value = on
            self.tg_3d.draw()
        if persist:
            settings.set_view_3d(on)
        if not on and self.spin:
            self._set_spin(False)
        self.view.set_mode3d(on)
        self.view.fit(self.view.visible)
        self.redraw()
        self._draw_gizmo()
        self._update_footer()

    def _set_spin(self, on: bool):
        on = bool(on)
        if on and not self.mode3d:
            self._set_mode3d(True)
        self.spin = on
        if self.tg_spin.value != on:
            self.tg_spin.value = on
            self.tg_spin.draw()
        if self._spin_job:
            self.root.after_cancel(self._spin_job)
            self._spin_job = None
        if on:
            self._spin_job = self.root.after(40, self._spin_step)
        else:
            self._schedule_depth_settle()

    def _spin_step(self):
        self._spin_job = None
        if not self.spin:
            return
        busy = self.drag is not None or bool(self.hand_ctrl.grab) or bool(self.hand_ctrl.rot_last)
        if not busy and self.nodes and self.view.rotate(SPIN_STEP * (2 if len(self.edge_items) > ROTATE_EDGE_LIMIT else 1), 0.0):
            self._render_frame(hide_edges=False)
            now = time.monotonic()
            if now - self._spin_shade_at > 1.0:   # gölge/sıra saniyede bir tazelenir (ucuz)
                self._spin_shade_at = now
                self._apply_highlight()
        interval = 80 if len(self.edge_items) > ROTATE_EDGE_LIMIT else 40
        self._spin_job = self.root.after(interval, self._spin_step)

    def _rotate(self, dyaw: float, dpitch: float):
        if self.view.rotate(dyaw, dpitch):
            self._request_frame()

    def _request_frame(self):
        """Hızlı ardışık döndürme olaylarını tek bir kareye topla (en fazla ~60 kare/sn)."""
        if self._frame_job is None:
            self._frame_job = self.root.after(15, self._render_frame)

    def _render_frame(self, hide_edges: bool = True):
        self._frame_job = None
        if not self.node_items:
            return
        skip_edges = hide_edges and len(self.edge_items) > ROTATE_EDGE_LIMIT
        if skip_edges and not self.edges_hidden:
            self.canvas.itemconfigure("edge", state="hidden")
            self.edges_hidden = True
        self.sync_nodes(list(self.node_items), edges=not skip_edges)
        self._update_labels()
        self.canvas.delete("hover")
        self._draw_gizmo()
        self._tick_fps()
        self._schedule_depth_settle()

    def _schedule_depth_settle(self):
        if self._depth_job:
            self.root.after_cancel(self._depth_job)
        self._depth_job = self.root.after(170, self._depth_settle)

    def _depth_settle(self):
        self._depth_job = None
        if self.edges_hidden:
            self.sync_nodes(list(self.node_items))
            self.canvas.itemconfigure("edge", state="normal")
            self.edges_hidden = False
        if self.view.mode3d:
            self._apply_highlight()

    def _draw_gizmo(self):
        """Sol altta küçük eksen göstergesi: 3B'de grafiğin hangi açıdan görüldüğünü gösterir."""
        c = self.canvas
        c.delete("gizmo")
        if not self.view.mode3d:
            return
        ox, oy, L = 46, max(60, self.view.height - 46), 26
        cos_y, sin_y, cos_p, sin_p = self.view._angles()
        axes = (("x", (1, 0, 0), C_ORG2), ("y", (0, -1, 0), C_GREEN), ("z", (0, 0, 1), C_BLUE))
        for name, (x, y, z), col in axes:
            x1 = x * cos_y + z * sin_y
            z1 = -x * sin_y + z * cos_y
            y2 = y * cos_p - z1 * sin_p
            c.create_line(ox, oy, ox + x1 * L, oy + y2 * L, fill=col, width=2, tags=("gizmo",))
            c.create_text(ox + x1 * (L + 9), oy + y2 * (L + 9), text=name, fill=col, font=fb(8, True),
                          tags=("gizmo",))
        c.create_text(ox, oy + L + 12, text="3B", fill=C_MID, font=fb(8, True), tags=("gizmo",))

    def _set_antialias(self, on: bool):
        """macOS Tk'de ince çizgilerin kenar yumuşatması çizim süresinin büyük kısmıdır (ölçüldü:
        2600 kenarda ~171 ms → ~23 ms). Küçük grafikte güzel, büyük grafikte hızlı çiz."""
        if sys.platform != "darwin":
            return
        try:
            self.root.tk.call("set", "::tk::mac::CGAntialiasLimit", 0 if on else 2)
        except tk.TclError:
            pass

    def sync_nodes(self, ids, edges: bool = True):
        c = self.canvas
        want_edges = edges
        edges = set()
        for nid in ids:
            item = self.node_items.get(nid)
            if item is None:
                continue
            x, y = self.view.node_screen(nid)
            r = self.view.screen_radius(nid)
            c.coords(item, x - r, y - r, x + r, y + r)
            h = self.halo_items.get(nid) if hasattr(self, "halo_items") else None
            if h is not None:
                hr = r * (2.3 if self.nodes[nid]["type"] == "project" else 1.9)
                c.coords(h, x - hr, y - hr, x + hr, y + hr)
            neb = self.nebula_items.get(nid) if hasattr(self, "nebula_items") else None
            if neb is not None:
                for nb, k in zip(*neb):
                    c.coords(nb, x - r * k, y - r * k, x + r * k, y + r * k)
            lab = self.label_items.get(nid)
            if lab is not None:
                c.coords(lab, x + r + 4, y)
            if want_edges:
                edges.update(self.node_edges.get(nid, ()))
        for idx in edges:
            item, e = self.edge_items[idx]
            x1, y1 = self.view.node_screen(e["src"])
            x2, y2 = self.view.node_screen(e["dst"])
            c.coords(item, x1, y1, x2, y2)

    def sync_all(self):
        self.sync_nodes(list(self.node_items))
        self._update_labels()
        self._tick_fps()
        if self.view.mode3d:
            self._draw_gizmo()
            self._schedule_depth_settle()   # pivot/açı değiştiyse gölge ve sıra da tazelensin

    def apply_pan(self, dx: float, dy: float):
        if dx or dy:
            self.canvas.move("world", dx, dy)
            self._tick_fps()

    def apply_zoom(self, factor: float, sx: float, sy: float):
        if len(self.node_items) <= EXACT_SYNC_LIMIT:
            self.sync_all()
        else:  # büyük grafikte tek C çağrısıyla ölçekle, kısa süre sonra kesin konumlara oturt
            self.canvas.scale("world", sx, sy, factor, factor)
            self.canvas.itemconfigure("label", state="hidden")
            self._schedule_settle()
            self._tick_fps()

    def _schedule_settle(self):
        if self._settle_job:
            self.root.after_cancel(self._settle_job)
        self._settle_job = self.root.after(160, self._settle)

    def _settle(self):
        self._settle_job = None
        self.sync_all()

    def _tick_fps(self):
        now = time.monotonic()
        self._frame_times.append(now)
        cutoff = now - 1.0
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.pop(0)
        self.render_fps = len(self._frame_times)

    def _draw_header(self):
        h = self.header
        h.delete("hdr")
        w = h.winfo_width()
        h.coords(self.header_btns, w - 8, 0)
        h.create_text(18, 20, text="◈ İKİNCİ BEYİN", anchor="w", fill=C_PRI, font=fd(17), tags=("hdr",))
        meta = self.graph["meta"] if self.graph else {}
        built = ""
        if meta.get("built_at"):
            built = datetime.fromtimestamp(meta["built_at"]).strftime("%d.%m %H:%M")
        sub = (f"{meta.get('nodes', 0)} düğüm · {meta.get('explicit_edges', 0)} açık · "
               f"{meta.get('inferred_edges', 0)} tahmini bağlantı · son tarama {built or '—'}")
        h.create_text(20, 44, text=sub, anchor="w", fill=C_MID, font=fb(9), tags=("hdr",))
        if self.tracker.running and not self.tracker.paused:
            h.create_text(330, 20, text="●  KAMERA AÇIK (yalnızca el takibi)", anchor="w", fill=C_RED,
                          font=fb(9, True), tags=("hdr",))
        h.create_line(0, 57, w, 57, fill=C_MID, tags=("hdr",))

    def _update_footer(self):
        vis = len(self.view.visible)
        total = len(self.nodes)
        parts = [f"{vis}/{total} düğüm gösteriliyor"]
        if getattr(self, "hidden_by_cap", 0):
            parts.append(f"akıcılık için en bağlantılı {MAX_VISIBLE} gösteriliyor — filtre/ara ile daralt")
        if self.hand_state == "active":
            fps = self.hand_stats.get("fps")
            cpu = self.hand_stats.get("cpu_percent")
            parts.append(f"el takibi {fps or '…'} fps · %{cpu or '…'} CPU · çizim {self.render_fps} fps")
        if self.hand_message:
            parts.append(self.hand_message)
        self.footer.configure(text="   ·   ".join(parts))
        self._draw_header()

    # ── ayrıntı paneli ───────────────────────────────────────────────────
    def _show_details(self, nid: str | None):
        d = self.details
        d.configure(state="normal")
        d.delete("1.0", "end")
        if not nid or nid not in self.nodes:
            d.insert("end", "Bir düğüme tıkla ya da el ile tut.\n\n", "muted")
            d.insert("end", "Her bağlantının neden kurulduğu ve hangi kaynağa dayandığı burada görünür.\n\n", "muted")
            d.insert("end", "AÇIK", "head_exp")
            d.insert("end", " = dosyada gerçekten yazan bağ (wiki/Markdown bağlantısı, #etiket, dosya/proje adı, "
                            "klasör konumu).\n", "reason")
            d.insert("end", "TAHMİNİ", "head_inf")
            d.insert("end", " = yerel modelin önerisi (içerik benzerliği, anahtar terim). Doğrulanmamıştır; "
                            "notların buluta gönderilmez.\n", "reason")
            d.configure(state="disabled")
            for b in (self.btn_open, self.btn_reveal, self.btn_neigh):
                b.configure_button(enabled=False)
            return
        n = self.nodes[nid]
        d.insert("end", f" {TYPE_NAME.get(n['type'], n['type'])} ", "badge_" + n["type"])
        d.insert("end", "\n")
        d.insert("end", n["label"] + "\n", "title")
        if n.get("path"):
            d.insert("end", n["path"].replace(str(Path.home()), "~") + "\n", "muted")
        info = []
        if n.get("mtime"):
            info.append("değişiklik " + datetime.fromtimestamp(n["mtime"]).strftime("%d.%m.%Y %H:%M"))
        if n.get("size") is not None and n["type"] in ("note", "file"):
            size = n["size"]
            info.append(f"{size / 1024:.1f} KB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB")
        if n["meta"].get("project_reason"):
            info.append("neden proje: " + n["meta"]["project_reason"])
        if n["type"] == "topic":
            origin = n["meta"].get("origin", "")
            info.append({"tag": "kaynak: notlardaki #etiket", "keyword": "kaynak: tahmini anahtar terim",
                         "tag+keyword": "kaynak: #etiket + anahtar terim"}.get(origin, origin))
        if n["meta"].get("extract", "").startswith(("pdf-metin", "okunamadı", "ikili")):
            info.append("metin okunamadı (" + n["meta"]["extract"] + ")")
        if info:
            d.insert("end", " · ".join(info) + "\n", "muted")
        if self.graph and n["type"] == "project":
            explicit, inferred = query.project_view(self.graph, nid, limit=80)
            own_exp, own_inf = query.describe_relations(self.graph, nid, limit=80)
            seen = {i["id"] for i in explicit}
            explicit += [i for i in own_exp if i["id"] not in seen]
            inferred += own_inf
        elif self.graph:
            explicit, inferred = query.describe_relations(self.graph, nid, limit=80)
        else:
            explicit, inferred = [], []
        d.insert("end", f"\nAÇIK BAĞLANTILAR ({len(explicit)})\n", "head_exp")
        if not explicit:
            d.insert("end", "Yok.\n", "muted")
        for i, item in enumerate(explicit):
            self._relation_row(item, "━", f"x{i}")
        d.insert("end", f"\nTAHMİNİ İLİŞKİLER ({len(inferred)})\n", "head_inf")
        d.insert("end", "Yerel modelin önerisi; kesin değildir.\n", "muted")
        for i, item in enumerate(inferred):
            self._relation_row(item, "┅", f"i{i}")
        unresolved = n["meta"].get("unresolved_links") or []
        if unresolved:
            d.insert("end", "\nÇözümlenemeyen bağlantılar (indekste yok): " + ", ".join(unresolved[:8]) + "\n", "warn")
        d.configure(state="disabled")
        has_file = bool(n.get("path")) and n["type"] in ("note", "file", "project")
        self.btn_open.configure_button(enabled=has_file and n["type"] != "project")
        self.btn_reveal.configure_button(enabled=has_file)
        self.btn_neigh.configure_button(enabled=True, text="TÜMÜ" if self.neighbor_mode else "KOMŞULAR")

    def _relation_row(self, item: dict, glyph: str, tag: str):
        d = self.details
        conf = f"  %{round(item['confidence'] * 100)}" if item.get("confidence") is not None else ""
        link = "go_" + tag
        d.insert("end", f"{glyph} {item['relation']} → ", "rel")
        d.insert("end", item["title"] + conf + "\n", ("rel", link))
        d.insert("end", "neden: " + item["reason"] + "\n", "reason")
        if item.get("evidence"):
            d.insert("end", "kaynak: " + item["evidence"] + "\n", "evidence")
        d.tag_config(link, underline=True, foreground=C_PRI if glyph == "━" else C_GOLD)
        d.tag_bind(link, "<Button-1>", lambda e, nid=item["id"]: self.select(nid, center=True))
        d.tag_bind(link, "<Enter>", lambda e: d.configure(cursor="hand2"))
        d.tag_bind(link, "<Leave>", lambda e: d.configure(cursor="arrow"))

    # ── seçim / arama ────────────────────────────────────────────────────
    def select(self, nid: str | None, center: bool = False):
        if nid and nid not in self.nodes:
            return
        self.selected = nid
        if nid and nid not in self.view.visible:
            self.view.visible.add(nid)
            self.view.set_visible(self.view.visible)
            self.redraw()
        if self.neighbor_mode:
            self._apply_filters()
        if center and nid:
            self.view.center_on(nid, scale=max(self.view.scale, 1.0))
            self.sync_all()
        self._apply_highlight()
        self._update_labels()
        self._show_details(nid)
        self._report_selection()

    def _report_selection(self):
        """JARVIS'e şu an neyin seçili/odakta olduğunu bildirir ("bunun içinde ne var?" sorusu için).
        Yalnızca değişince gönderilir; dosya içeriği gönderilmez, JARVIS indeksten okur."""
        nid = self.selected if self.selected in self.nodes else None
        state = (nid, bool(self.neighbor_mode and nid))
        if state == getattr(self, "_reported_selection", None):
            return
        self._reported_selection = state
        node = self.nodes.get(nid) if nid else None
        emit({"event": "selection", "id": nid, "label": node["label"] if node else "",
              "type": node["type"] if node else "", "path": node.get("path", "") if node else "",
              "focus": state[1]})

    def _focus_search(self):
        self.search.focus_set()
        self.search.select_range(0, "end")

    def _on_search_key(self, _e=None):
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(220, self._run_search)

    def _run_search(self):
        self._search_job = None
        text = self.search_var.get().strip()
        if not text or not self.graph:
            self._clear_search(keep_text=True)
            return
        hits = [n for s, n in query.match_nodes(self.graph, text, limit=40) if s >= 5.0]
        ids = {n["id"] for n in hits}
        content = query.content_matches(self.graph, text, settings.DB_PATH, limit=20, exclude=ids)
        ids |= {c["id"] for c in content}
        self.search_hits = ids
        missing = ids - self.view.visible
        if missing:
            self._apply_filters()
        else:
            self._apply_highlight()
            self._update_labels()
        self.search_info.configure(text=f"{len(ids)} eşleşme (ad/etiket {len(hits)}, metin {len(content)})"
                                   if ids else "Eşleşme yok (yalnızca indekslenen klasörler)")

    def _on_search_enter(self, _e=None):
        self._run_search()
        if self.graph:
            hits = query.match_nodes(self.graph, self.search_var.get(), limit=1)
            target = hits[0][1]["id"] if hits and hits[0][0] >= 5 else next(iter(self.search_hits), None)
            if target:
                self.select(target, center=True)

    def _clear_search(self, keep_text: bool = False):
        if not keep_text:
            self.search_var.set("")
        self.search_hits = set()
        self.search_info.configure(text="Ad, etiket veya metin · Enter: git")
        self._apply_filters()

    def focus_query(self, text: str):
        """JARVIS'ten gelen 'bunu göster' isteği: aramayı doldur ve en iyi eşleşmeye odaklan."""
        self.search_var.set(text)
        self._on_search_enter()
        self.raise_window()

    # ── filtreler ────────────────────────────────────────────────────────
    def _set_type(self, t: str, value: bool):
        self.type_on[t] = value
        self._apply_filters()

    def _set_explicit(self, value: bool):
        self.show_explicit = value
        self.redraw()

    def _set_inferred(self, value: bool):
        self.show_inferred = value
        self.redraw()

    def _set_conf(self, value):
        self.min_conf = float(value)
        self.conf_label.configure(text=f"Tahmin için en düşük güven: %{int(self.min_conf)}")
        if self.nodes:
            self.redraw()

    def _refresh_source_menu(self):
        menu = self.source_menu["menu"]
        menu.delete(0, "end")
        roots = sorted({n.get("root") for n in self.nodes.values() if n.get("root")})

        def choose(value, label):
            self.source_filter = value
            self.source_var.set(label)
            self._apply_filters()

        menu.add_command(label="Tüm kaynaklar", command=lambda: choose("", "Tüm kaynaklar"))
        for root in roots:
            menu.add_command(label=Path(root).name, command=lambda r=root: choose(r, Path(r).name))

    def toggle_neighbors(self):
        if not self.selected:
            return
        self.neighbor_mode = not self.neighbor_mode
        self._apply_filters()
        if self.neighbor_mode:
            self.view.fit(self.view.visible)
            self.sync_all()
        self._show_details(self.selected)

    # ── görünüm ──────────────────────────────────────────────────────────
    def reset_view(self):
        """Sıfırla: elle taşınanları geri koy, filtre/odak temizle, tümünü sığdır."""
        self.neighbor_mode = False
        self.view.reset()
        self._apply_filters(redraw=False)
        self.redraw()
        self._draw_gizmo()
        self._update_footer()

    def _zoom_center(self, factor: float, sx: float | None = None, sy: float | None = None):
        sx = self.view.width / 2 if sx is None else sx
        sy = self.view.height / 2 if sy is None else sy
        f = self.view.zoom_at(factor, sx, sy)
        if f != 1.0:
            self.apply_zoom(f, sx, sy)

    def _on_resize(self, e):
        self.view.resize(e.width, e.height)
        if not self.nodes:
            self._draw_empty()
        elif not self._fitted and e.width > 300:
            # Pencere ilk kez gerçek boyutuna geldiğinde grafiği sığdır.
            self._fitted = True
            self.view.fit()
            self.sync_all()
            if self.selected:
                self._apply_highlight()
        if self.nodes:
            self._draw_starfield()
        self._draw_gizmo()
        self._place_hud()

    # ── fare ─────────────────────────────────────────────────────────────
    def _on_press(self, e):
        self.canvas.focus_set()
        nid = self.view.hit_test(e.x, e.y)
        if nid:
            x, y = self.view.node_screen(nid)
            self.drag = {"node": nid, "dx": x - e.x, "dy": y - e.y, "moved": False}
            self.select(nid)
        elif self.view.mode3d and not (getattr(e, "state", 0) & 0x1):   # ⇧ basılı değilse döndür
            self.drag = {"rotate": (e.x, e.y), "moved": False}
        else:
            self.drag = {"pan": (e.x, e.y), "moved": False}

    def _on_pan_press(self, e):
        self.canvas.focus_set()
        self.drag = {"pan": (e.x, e.y), "moved": False}

    def _on_drag(self, e):
        if not self.drag:
            return
        if "node" in self.drag:
            self.view.move_node_screen(self.drag["node"], e.x + self.drag["dx"], e.y + self.drag["dy"])
            self.sync_nodes([self.drag["node"]])
            self._tick_fps()
        elif "rotate" in self.drag:
            lx, ly = self.drag["rotate"]
            # Sağa sürükle → öndeki yüz sağa gelir (küreyi tutup çevirir gibi).
            self._rotate(-(e.x - lx) * ROT_PER_PX, (e.y - ly) * ROT_PER_PX)
            self.drag["rotate"] = (e.x, e.y)
        else:
            lx, ly = self.drag["pan"]
            self.apply_pan(*self.view.pan(e.x - lx, e.y - ly))
            self.drag["pan"] = (e.x, e.y)
        self.drag["moved"] = True

    def _on_release(self, _e):
        if self.drag and ("pan" in self.drag or "rotate" in self.drag) and not self.drag["moved"]:
            self.select(None)
        self.drag = None

    def _on_double(self, e):
        nid = self.view.hit_test(e.x, e.y)
        if self.drag is not None:
            self.drag["moved"] = True   # çift tıkın ikinci basışı "boşluğa tıkla = seçimi kaldır" sayılmasın
        if nid is None:
            if self.neighbor_mode:
                self.focus_node(None)          # boşluğa çift tık: odaktan çık, tümünü göster
            return
        if self.nodes[nid]["type"] in FOCUS_TYPES:
            self.focus_node(nid)               # sarı proje / konu dairesi: bağlı olanlara yakınlaş
        else:
            self.select(nid)
            self.open_selected()

    # ── odak: bir düğüme ve ona bağlı olanlara yakınlaş, gerisini gizle ──
    def focus_node(self, nid: str | None, source: str = "fare"):
        """Çift tık / çift sıkıştırma. Aynı düğüme tekrar ya da boşluğa çift tık odaktan çıkarır."""
        if nid is not None and nid not in self.nodes:
            return
        if nid is None or (self.neighbor_mode and self.selected == nid):
            if not self.neighbor_mode:
                return
            self.neighbor_mode = False
            self._apply_filters()
            self._animate_fit(self.view.visible)
            self._show_details(self.selected)
            self.hand_message = "Odak kapatıldı; tüm grafik gösteriliyor."
            self._update_footer()
            self._report_selection()
            return
        self.neighbor_mode = True
        self.select(nid)                       # komşu kipinde görünür küme = düğüm + bağlı olanlar
        self._animate_fit(self.view.visible)
        count = len(self.view.visible) - 1
        how = {"el": "el ile iki kez sıkıştır", "ses": "‘odaktan çık’ de"}.get(source, "çift tıkla")
        self.hand_message = (f"ODAK: ‘{self.nodes[nid]['label'][:40]}’ ve bağlı {count} öğe · "
                             f"çıkmak için yine {how} ya da Esc")
        self._update_footer()
        self._report_selection()

    def _fist_clear_selection(self):
        """Tek yumruk (04.10 kullanıcı isteği): tek dokunuşla seçilen dosya ve bağlı olanların vurgusundan çık.
        Odak (çift dokunuş) ve yakınlaştırma için çift yumruk gerekir; odaktayken tek yumruk bir şey yapmaz."""
        if self.neighbor_mode or not self.selected:
            return
        label = self.nodes.get(self.selected, {}).get("label", "")
        self.select(None)
        self.hand_message = f"Yumruk: ‘{label[:40]}’ seçimi kaldırıldı; tüm grafik gösteriliyor."
        self._update_footer()

    def unzoom_view(self, source: str = "el"):
        """Çift yumruk: yakınlaştırmadan / odaktan çıkar. SIFIRLAMAZ — taşınan düğümler, 3B açı,
        filtreler ve seçim olduğu gibi kalır; yalnızca görünür kümenin tamamı ekrana sığdırılır."""
        if self.neighbor_mode:
            self.focus_node(None, source=source)
            self.hand_message = "Çift yumruk: odaktan çıkıldı; tüm grafik gösteriliyor (sıfırlanmadı)."
            self._update_footer()
            return
        v = self.view
        ids = v.visible
        if not ids:
            return
        saved = (v.scale, v.ox, v.oy, v.pivot, v.cam_dist, v.extent)
        v.fit(ids)
        target = v.scale
        v.scale, v.ox, v.oy, v.pivot, v.cam_dist, v.extent = saved
        v.invalidate()
        if saved[0] <= target * 1.05:
            self.hand_message = "Zaten uzak görünümdesin; çıkılacak yakınlaştırma yok."
        else:
            self._animate_fit(ids)
            self.hand_message = "Çift yumruk: yakınlaştırmadan çıkıldı (düğümler ve açı korundu)."
        self._update_footer()

    def _animate_fit(self, ids):
        """Görünümü hedef kümeye yumuşakça yakınlaştırır (≈0,2 sn)."""
        v = self.view
        if self._anim_job:
            self.root.after_cancel(self._anim_job)
            self._anim_job = None
        start = (v.scale, v.ox, v.oy, v.pivot, v.cam_dist, v.extent)
        v.fit(ids)
        end = (v.scale, v.ox, v.oy, v.pivot, v.cam_dist, v.extent)
        w2, h2 = v.width / 2, v.height / 2
        c0 = ((w2 - start[1]) / start[0], (h2 - start[2]) / start[0])
        c1 = ((w2 - end[1]) / end[0], (h2 - end[2]) / end[0])

        def lerp(a, b, t):
            return a + (b - a) * t

        def step(k):
            self._anim_job = None
            t = k / FOCUS_ANIM_FRAMES
            e = 1.0 - (1.0 - t) ** 3            # yavaşlayarak dur
            scale = start[0] * (end[0] / start[0]) ** e
            cu, cv = lerp(c0[0], c1[0], e), lerp(c0[1], c1[1], e)
            v.scale = scale
            v.ox, v.oy = w2 - cu * scale, h2 - cv * scale
            v.pivot = tuple(lerp(a, b, e) for a, b in zip(start[3], end[3]))
            v.cam_dist, v.extent = lerp(start[4], end[4], e), lerp(start[5], end[5], e)
            if k >= FOCUS_ANIM_FRAMES:
                v.scale, v.ox, v.oy, v.pivot, v.cam_dist, v.extent = end
            v.invalidate()
            self.sync_all()
            if k < FOCUS_ANIM_FRAMES:
                self._anim_job = self.root.after(16, step, k + 1)
            else:
                self._apply_highlight()

        step(1)

    def _on_wheel(self, e):
        delta = e.delta
        if sys.platform != "darwin":
            delta = delta / 120.0
        factor = math.exp(max(-6.0, min(6.0, delta)) * 0.05)
        self._zoom_center(factor, e.x, e.y)

    def _on_motion(self, e):
        if self.drag or len(self.node_items) > 2500:
            return
        nid = self.view.hit_test(e.x, e.y)
        c = self.canvas
        c.delete("hover")
        if nid and nid in self.nodes and nid not in self.focus_ids:
            x, y = self.view.node_screen(nid)
            r = self.view.screen_radius(nid)
            c.create_text(x + r + 4, y, text=self.nodes[nid]["label"], anchor="w", fill=C_TEXT,
                          font=fb(9), tags=("hover",))

    def _on_key(self, e):
        if self.root.focus_get() is self.search:
            return
        k = e.keysym
        if k in ("plus", "equal", "KP_Add"):
            self._zoom_center(1.2)
        elif k in ("minus", "KP_Subtract"):
            self._zoom_center(1 / 1.2)
        elif k in ("0", "r", "R"):
            self.reset_view()
        elif k == "Escape":
            if self.neighbor_mode:
                self.focus_node(None)
            elif self.search_hits:
                self._clear_search()
            else:
                self.select(None)
        elif k in ("Left", "Right", "Up", "Down"):
            if self.view.mode3d and not (getattr(e, "state", 0) & 0x1):
                dyaw = {"Left": KEY_ROT, "Right": -KEY_ROT}.get(k, 0.0)
                dpitch = {"Up": -KEY_ROT, "Down": KEY_ROT}.get(k, 0.0)
                self._rotate(dyaw, dpitch)
            else:
                dx = {"Left": 60, "Right": -60}.get(k, 0)
                dy = {"Up": 60, "Down": -60}.get(k, 0)
                self.apply_pan(*self.view.pan(dx, dy))
        elif k == "3":
            self._set_mode3d(not self.mode3d)
        elif k in ("d", "D"):
            self._set_spin(not self.spin)
        elif k in ("h", "H"):
            self.toggle_hands()
        elif k == "slash":
            self._focus_search()

    # ── dosya işlemleri (yalnızca fare/klavye; el hareketi asla dosya açmaz) ──
    def _selected_path(self) -> str | None:
        if not self.selected or self.selected not in self.nodes:
            return None
        path = self.nodes[self.selected].get("path")
        if not path or not os.path.exists(path):
            self.hand_message = "Dosya artık bu konumda yok; yeniden tarayın."
            self._update_footer()
            return None
        roots = [str(Path(s)) for s in settings.get_sources()]
        if not any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots):
            self.hand_message = "Bu dosya artık seçili kaynak klasörlerin içinde değil."
            self._update_footer()
            return None
        return path

    def open_selected(self):
        path = self._selected_path()
        if path and self.nodes[self.selected]["type"] != "project":
            self._open(["open", path])

    def reveal_selected(self):
        path = self._selected_path()
        if path:
            self._open(["open", "-R", path])

    def _open(self, cmd):
        if sys.platform != "darwin":
            cmd = ["xdg-open", cmd[-1]]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.hand_message = f"Açılamadı: {exc}"
            self._update_footer()

    # ── kaynak ve tarama ─────────────────────────────────────────────────
    def add_source(self):
        path = filedialog.askdirectory(parent=self.root, title="İkinci beyne eklenecek not/proje klasörü",
                                       mustexist=True)
        if not path:
            return
        ok, message, _ = settings.add_source(path)
        self.hand_message = message
        self._update_footer()
        if ok:
            emit({"event": "sources_changed"})
            self.reindex()

    def reindex(self):
        if self._index_proc is not None:
            return
        if not settings.get_sources():
            self.hand_message = "Önce ‘+ KAYNAK EKLE’ ile klasör seç."
            self._update_footer()
            return
        cmd = [sys.executable, "-m", "brain.indexer", "--build"]
        try:
            self._index_proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as exc:
            self.hand_message = f"Tarama başlatılamadı: {exc}"
            self._update_footer()
            return
        self.btn_reindex.configure_button(text="TARANIYOR…", enabled=False)
        if not self.nodes:
            self._draw_empty()
        proc = self._index_proc

        def reader():
            last = None
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    last = json.loads(line)
                except ValueError:
                    continue
                self.cmd_queue.put({"cmd": "_index_progress", "event": last})
            proc.wait()
            self.cmd_queue.put({"cmd": "_index_done", "event": last or {}})

        threading.Thread(target=reader, daemon=True).start()

    def _maybe_reindex(self):
        st = query.status(settings.DB_PATH)
        if not st["sources"] or st["indexing"]:
            if st["indexing"]:
                self.root.after(2000, self._maybe_reindex)
            return
        meta = self.graph["meta"] if self.graph else {}
        old = time.time() - float(meta.get("built_at") or 0) > 1800
        if not self.graph or st["stale_sources"] or old:
            self.reindex()

    # ── ana süreç komutları ──────────────────────────────────────────────
    def _stdin_reader(self):
        try:
            for line in sys.stdin:
                try:
                    self.cmd_queue.put(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        self.cmd_queue.put({"cmd": "quit"})  # ana JARVIS kapandı

    def _poll_commands(self):
        try:
            while not getattr(self, "_closed", False):
                msg = self.cmd_queue.get_nowait()
                self._handle_command(msg)
        except queue.Empty:
            pass
        if getattr(self, "_closed", False):
            return
        try:
            self.root.after(250, self._poll_commands)
        except tk.TclError:  # pencere kapatılırken
            pass

    def _handle_command(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "quit":
            self.close()
        elif cmd == "focus":
            self.focus_query(str(msg.get("query", "")))
        elif cmd == "raise":
            self.raise_window()
        elif cmd == "control":
            self._control(msg)
        elif cmd == "reload":
            self.load_graph()
        elif cmd == "reindex":
            self.reindex()
        elif cmd == "hand":
            self.set_hands(bool(msg.get("enabled")), persist=False)
        elif cmd == "_hand_check":
            self._on_hand_check(msg["res"])
        elif cmd == "_install_log":
            self.footer.configure(text="Kurulum: " + str(msg.get("line", ""))[-140:])
        elif cmd == "_install_done":
            self._on_install_done(msg["res"])
        elif cmd == "_index_progress":
            ev = msg.get("event") or {}
            phase = {"scan": "klasörler taranıyor", "extract": "metin okunuyor", "pdf": "PDF'ler okunuyor",
                     "graph": "bağlantılar kuruluyor", "layout": "yerleşim hesaplanıyor"}.get(ev.get("phase"), "")
            if phase:
                self.footer.configure(text="Tarama: " + phase + "…")
        elif cmd == "_index_done":
            self._index_proc = None
            self.btn_reindex.configure_button(text="↻  YENİDEN TARA", enabled=True)
            ev = msg.get("event") or {}
            if ev.get("type") == "done":
                self.hand_message = (f"Tarama bitti: {ev.get('files', 0)} dosya, {ev.get('edges', 0)} bağlantı"
                                     + (" (dosya sınırına ulaşıldı)" if ev.get("truncated") else ""))
                self.load_graph()
                emit({"event": "indexed", "stats": {k: ev.get(k) for k in ("files", "nodes", "edges")}})
            elif ev.get("type") in ("error", "busy"):
                self.hand_message = "Tarama: " + str(ev.get("message", ""))
                if ev.get("type") == "busy":
                    self.root.after(3000, self.load_graph)
            self._update_footer()

    # ── JARVIS sesli komutları (plan brain.voice_control'da yapılır) ─────────
    def _control(self, msg: dict):
        op = msg.get("op")
        note = ""
        before = self.hand_message
        if op == "select":
            nid = msg.get("id")
            if nid in self.nodes:
                self.select(nid, center=True)
                note = f"JARVIS: ‘{self.nodes[nid]['label']}’ seçildi"
            else:
                note = "JARVIS: bu düğüm grafikte yok (yeniden tarama gerekebilir)"
        elif op == "zoom":
            try:
                factor = max(0.2, min(5.0, float(msg.get("factor", 1.25))))
            except (TypeError, ValueError):
                factor = 1.25
            self._zoom_center(factor)
            note = "JARVIS: " + ("yakınlaştırıldı" if factor > 1 else "uzaklaştırıldı")
        elif op == "focus":
            nid = msg.get("id")
            if nid is None:
                if self.neighbor_mode:
                    self.focus_node(None, source="ses")
                    note = "JARVIS: odaktan çıkıldı; tüm grafik gösteriliyor"
                else:
                    note = "JARVIS: zaten odakta değil"
            elif nid not in self.nodes:
                note = "JARVIS: bu düğüm grafikte yok (yeniden tarama gerekebilir)"
            elif self.neighbor_mode and self.selected == nid:
                note = f"JARVIS: zaten ‘{self.nodes[nid]['label']}’ odağında"
            else:
                self.focus_node(nid, source="ses")
                note = f"JARVIS: ‘{self.nodes[nid]['label']}’ odağına geçildi"
        elif op == "fit":
            self.view.fit(self.view.visible)
            self.sync_all()
            note = "JARVIS: tümü ekrana sığdırıldı"
        elif op == "clear":
            self.neighbor_mode = False
            self._clear_search()
            self.select(None)
            note = "JARVIS: seçim ve arama temizlendi"
        elif op == "neighbors":
            on = bool(msg.get("on", True))
            if on and not self.selected:
                note = "JARVIS: komşuları göstermek için önce bir düğüm seçilmeli"
            else:
                self.neighbor_mode = on
                self._apply_filters()
                if on:
                    self.view.fit(self.view.visible)
                    self.sync_all()
                self._show_details(self.selected)
                note = "JARVIS: " + ("yalnızca komşular gösteriliyor" if on else "komşu odağı kapatıldı")
        elif op == "filter":
            rel = msg.get("relations")
            if rel in ("all", "explicit", "inferred"):
                self.show_explicit = rel in ("all", "explicit")
                self.show_inferred = rel in ("all", "inferred")
                for tg, val in ((self.tg_explicit, self.show_explicit), (self.tg_inferred, self.show_inferred)):
                    tg.value = val
                    tg.draw()
            types = msg.get("types")
            if isinstance(types, list) and types:
                for t in self.type_on:
                    self.type_on[t] = t in types
                    self.type_toggles[t].value = self.type_on[t]
                    self.type_toggles[t].draw()
            if msg.get("min_conf") is not None:
                value = max(0, min(90, int(msg["min_conf"])))
                try:
                    self.conf_scale.set(value)
                except tk.TclError:
                    pass
                self._set_conf(value)
            if "source" in msg:
                self.source_filter = str(msg.get("source") or "")
                self.source_var.set(Path(self.source_filter).name if self.source_filter else "Tüm kaynaklar")
            self._apply_filters()
            note = "JARVIS: filtre uygulandı"
        elif op == "rotate":
            if not self.mode3d:
                self._set_mode3d(True)
            try:
                dyaw = math.radians(max(-360.0, min(360.0, float(msg.get("yaw", 0) or 0))))
                dpitch = math.radians(max(-170.0, min(170.0, float(msg.get("pitch", 0) or 0))))
            except (TypeError, ValueError):
                dyaw = dpitch = 0.0
            self._rotate(dyaw, dpitch)
            note = "JARVIS: grafik döndürüldü"
        elif op == "view":
            on = bool(msg.get("mode3d", True))
            self._set_mode3d(on)
            note = "JARVIS: " + ("3B görünüm açık" if on else "2B görünüm")
        elif op == "spin":
            on = bool(msg.get("on", True))
            self._set_spin(on)
            note = "JARVIS: " + ("otomatik döndürme açık" if on else "otomatik döndürme durdu")
        elif op == "open":
            if self.selected:
                self.open_selected()
                note = f"JARVIS: ‘{self.nodes[self.selected]['label']}’ açılıyor"
            else:
                note = "JARVIS: açmak için seçili dosya yok"
        elif op == "reveal":
            if self.selected:
                self.reveal_selected()
                note = "JARVIS: Finder'da gösteriliyor"
            else:
                note = "JARVIS: gösterilecek seçili dosya yok"
        if note and self.hand_message == before:   # işlem kendi uyarısını yazdıysa onu koru
            self.hand_message = note
        self._update_footer()
        self.raise_window()

    def raise_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    # ── el kontrolü ──────────────────────────────────────────────────────
    def toggle_hands(self):
        self.set_hands(not self.hand_enabled)

    def set_hands(self, enabled: bool, persist: bool = True):
        self.hand_enabled = enabled
        if persist:
            try:
                settings.set_hand_control(enabled)
            except OSError:
                pass
            emit({"event": "hand_control", "enabled": enabled})
        if enabled:
            self.start_hands()
        else:
            self.stop_hands("El kontrolü kapalı; kamera kullanılmıyor.")

    def _refresh_hand_button(self):
        text, col = {
            "off": ("✋ EL KONTROLÜ: KAPALI", C_MID),
            "checking": ("✋ DENETLENİYOR…", C_GOLD),
            "installing": ("✋ KURULUYOR…", C_GOLD),
            "starting": ("✋ KAMERA AÇILIYOR…", C_GOLD),
            "active": ("✋ EL KONTROLÜ: AÇIK", C_GREEN),
            "paused": ("✋ BEKLEMEDE (tıkla)", C_GOLD),
            "needs_install": ("✋ EL TAKİBİNİ KUR", C_BLUE),
            "error": ("✋ EL KONTROLÜ: HATA", C_RED),
        }.get(self.hand_state, ("✋ EL KONTROLÜ", C_MID))
        self.btn_hand.configure_button(text=text, color=col)
        cmd = self.toggle_hands
        if self.hand_state == "needs_install":
            cmd = self.install_hands
        elif self.hand_state == "paused":
            cmd = self.resume_hands
        self.btn_hand.command = cmd

    def start_hands(self):
        if self.tracker.running or self.hand_state in ("checking", "starting", "installing"):
            return   # zaten başlıyor (ör. açılış + JARVIS'in "el kontrolünü aç" komutu aynı anda)
        self.hand_state = "checking"
        self.hand_message = "El takibi bileşeni denetleniyor…"
        self._refresh_hand_button()
        self._update_footer()

        def work():
            self.cmd_queue.put({"cmd": "_hand_check", "res": hand_setup.check()})

        threading.Thread(target=work, daemon=True).start()

    def _on_hand_check(self, res: dict):
        if not self.hand_enabled:
            self.hand_state = "off"
            self._refresh_hand_button()
            return
        if res["status"] != "ready":
            self.hand_state = "needs_install"
            self.hand_message = res["message"]
            self._refresh_hand_button()
            self._update_footer()
            emit({"event": "hand_status", "status": res["status"], "message": res["message"]})
            return
        self.hand_state = "starting"
        self.hand_message = "Kamera açılıyor (görüntü yalnızca bu Mac'te işlenir)…"
        self._refresh_hand_button()
        self._update_footer()
        self.engine = GestureEngine()
        self.hand_camera_active = None
        # El takibi YALNIZCA Mac'in kendi (yerleşik) kamerasını kullanır; başka kameraya bağlanmaz.
        self.tracker.camera_args = ["--camera", "builtin", "--camera-uid", settings.hand_camera_uid()]
        if self.tracker.start():
            self.hand_last_seen = time.monotonic()
            self.hand_last_frame = time.monotonic()
            self._show_hud(True)
            if self._hand_job is None:   # tek döngü
                self._hand_loop()
        else:
            self._drain_tracker_messages()

    def install_hands(self):
        if self.hand_state == "installing":
            return
        self.hand_state = "installing"
        self.hand_message = "El takibi kuruluyor (bir kerelik, birkaç dakika)…"
        self._refresh_hand_button()
        self._update_footer()

        def log(line):
            self.cmd_queue.put({"cmd": "_install_log", "line": line})

        def work():
            self.cmd_queue.put({"cmd": "_install_done", "res": hand_setup.install(log)})

        threading.Thread(target=work, daemon=True).start()

    def _on_install_done(self, res: dict):
        if res["status"] == "ready":
            self.hand_message = "El takibi kuruldu."
            self.hand_state = "off"
            if self.hand_enabled:
                self.start_hands()
        else:
            self.hand_state = "needs_install"
            self.hand_message = res["message"]
        self._refresh_hand_button()
        self._update_footer()

    # ── kamera (yalnızca Mac'in kendi kamerası) ─────────────────────────
    def _note_camera(self, msg: dict):
        idx = msg.get("camera")
        if isinstance(idx, int) and idx >= 0:
            self.hand_camera_active = idx
        cam = msg.get("builtin") or {}
        if cam.get("uid"):
            self.hand_camera_name = cam.get("name") or "Mac kamerası"
            try:
                settings.set_hand_camera_uid(cam["uid"])   # bulunan yerleşik kamerayı sabitle
            except OSError:
                pass
        self.btn_camera.configure_button(text=self._camera_button_text())

    def _camera_label(self) -> str:
        return self.hand_camera_name or "Mac kamerası"

    def _camera_button_text(self) -> str:
        return "◉ KAMERA: MAC"

    def show_camera_info(self):
        self.hand_message = (f"El takibi yalnızca bu Mac'in kendi kamerasını kullanır ({self._camera_label()}); "
                             "sanal kamera, iPhone veya harici kameralara bağlanmaz.")
        self._update_footer()

    def resume_hands(self):
        self._unmap_paused = False
        self.tracker.resume()
        self.hand_state = "active"
        self.hand_last_seen = time.monotonic()
        self.hand_last_frame = time.monotonic()   # yoksa ilk turda yanlış "yanıt vermiyor" uyarısı
        self.hand_message = ""
        self._refresh_hand_button()
        self._update_footer()

    def stop_hands(self, message: str = ""):
        if self._hand_job:
            self.root.after_cancel(self._hand_job)
            self._hand_job = None
        changes = self.hand_ctrl.apply(self.engine.clear())
        self._render_hand_changes(changes)
        self.tracker.stop()
        self.hand_state = "off"
        self.hand_message = message
        self._show_hud(False)
        self._refresh_hand_button()
        self._update_footer()

    def _drain_tracker_messages(self):
        while True:
            try:
                msg = self.tracker.messages.get_nowait()
            except queue.Empty:
                return
            t = msg.get("type")
            if t != "stats":
                _hlog(json.dumps(msg, ensure_ascii=False)[:300])
            if t == "ready":
                self.hand_state = "active"
                self.hand_last_frame = time.monotonic()
                self._show_hud(True)   # önceki hata katmanı gizlemişse geri getir
                self._note_camera(msg)
                self.hand_message = (f"El kontrolü açık ({self._camera_label()}): sıkıştır = tut, iki el = "
                                     "yakınlaştır · ✌ 1 sn = sıfırla")
                self._refresh_hand_button()
            elif t == "stats":
                self.hand_stats = msg
                if time.monotonic() - self._hand_log_at >= 30:
                    two = self.engine.pop_counts()
                    _hlog(f"fps={msg.get('fps')} boşta={msg.get('idle')} kare={self._hand_frames[0]} "
                          f"eli_görülen={self._hand_frames[1]} durum={self.hand_state} "
                          f"iki_el_ham={two['raw2']} iki_el_geçerli={two['valid2']} hayalet={two['ghost']}")
                    self._hand_frames = [0, 0]
                    self._hand_log_at = time.monotonic()
                emit({"event": "hand_stats", **{k: msg.get(k) for k in ("fps", "proc_ms", "cpu_percent", "idle")},
                      "render_fps": self.render_fps})
            elif t == "status":
                state = msg.get("state")
                if state == "permission_prompt":
                    self.hand_message = msg.get("message", "")
                elif state == "paused":
                    if not self.tracker.paused:
                        pass  # duraklatma bu arada geri alındı; "resumed" gelecek
                    else:
                        self.hand_state = "paused"
                        self._refresh_hand_button()
                elif state == "resumed":
                    self.hand_state = "active"
                    self.hand_last_frame = time.monotonic()
                    self._refresh_hand_button()
                elif state == "frozen":
                    self.hand_message = msg.get("message", "Mac kamerasının görüntüsü dondu; yeniden açılıyor…")
            elif t == "error":
                code = msg.get("code", "internal")
                self.hand_message = hand_setup.EXPLAIN.get(code, msg.get("message", "El takibi hatası."))
                self.hand_state = "needs_install" if code in ("missing_dependency", "missing_env", "model_missing") else "error"
                emit({"event": "hand_status", "status": code, "message": self.hand_message})
                self.tracker.stop()
                self._show_hud(False)
                self._refresh_hand_button()
            elif t == "exited" and self.hand_state in ("active", "starting"):
                self.hand_state = "error"
                self.hand_message = hand_setup.EXPLAIN["internal"]
                self._show_hud(False)
                self._refresh_hand_button()
            self._update_footer()

    def _hand_loop(self):
        self._hand_job = None
        self._drain_tracker_messages()
        if not self.tracker.running and self.hand_state not in ("starting",):
            if self.hand_state == "active":
                self.hand_state = "error"
            self._refresh_hand_button()
            return
        now = time.monotonic()
        frame = self.tracker.take_latest()
        if frame is not None:
            self.hand_last_frame = now
            if self.hand_message == "El takibi yanıt vermiyor…":
                self.hand_message = ""
                self._update_footer()
            w, h = frame.get("w") or 640, frame.get("h") or 480
            result = self.engine.update(frame.get("hands", []), frame.get("t", now), w / float(h))
            for line in self.engine.pop_diag():      # sıkıştırma teşhisi → İkinci Beyin günlüğü (eşik ayarı için)
                _hlog(line)
            self._hand_frames[0] += 1
            if result["hands"]:
                self.hand_last_seen = now
                self._hand_frames[1] += 1
            changes = self.hand_ctrl.apply(result["events"], now=frame.get("t", now))
            self._render_hand_changes(changes)
            if any(ev[0] == "unzoom" for ev in result["events"]):
                self.unzoom_view(source="el")
            elif any(ev[0] == "fist" for ev in result["events"]):
                self._fist_clear_selection()
            self._draw_hud(result)
        elif self.hand_state == "active" and now - self.hand_last_frame > 3.0 and not self.tracker.paused:
            self.hand_message = "El takibi yanıt vermiyor…"
            self._update_footer()
        # 5 dk el görülmezse kamerayı kapat (pil/işlemci tasarrufu); tıklayınca sürer.
        if self.hand_state == "active" and now - self.hand_last_seen > 300:
            self.tracker.pause()
            self.hand_state = "paused"
            self.hand_message = "5 dakikadır el görülmedi; kamera kapatıldı. Sürdürmek için düğmeye tıkla."
            self._refresh_hand_button()
            self._update_footer()
        self._hand_job = self.root.after(16, self._hand_loop)   # ~60 Hz: kare gelir gelmez çiz

    def _render_hand_changes(self, ch: dict):
        if ch["reset"]:
            self.reset_view()
            self.hand_message = "✌ ile sıfırlandı: tüm grafik başlangıç görünümünde."
            return
        if ch["pan"][0] or ch["pan"][1]:
            self.apply_pan(*ch["pan"])
        if ch.get("rotate"):
            self._request_frame()
        if ch.get("double") is not None:
            # Çift sıkıştırma = çift tık; el asla dosya açmaz, yalnızca odaklanır.
            self.focus_node(ch["double"] or None, source="el")
            return
        for f, sx, sy in ch["zoom"]:
            self.apply_zoom(f, sx, sy)
        if ch["moved"]:
            self.sync_nodes(ch["moved"])
            self._tick_fps()
        if ch["selected"] and ch["selected"] != self.selected:
            self.select(ch["selected"])

    # ── el HUD'u ─────────────────────────────────────────────────────────
    def _show_hud(self, on: bool):
        c = self.canvas
        if not on:
            c.delete("hud")
            self.hud_items = {}
            self.hover_id = None
            return
        if self.hud_items:
            return
        c.delete("hud")
        t = ("hud",)
        items = {
            "panel": c.create_rectangle(0, 0, 1, 1, fill="#08181c", outline=C_DIM, tags=t),
            "title": c.create_text(0, 0, text="EL KONTROLÜ", anchor="nw", fill=C_PRI, font=fb(9, True), tags=t),
            "quality": c.create_text(0, 0, text="", anchor="ne", fill=C_MID, font=fb(9, True), tags=t),
            "qbars": [c.create_rectangle(0, 0, 1, 1, fill=C_DIM, outline="", tags=t) for _ in range(3)],
            "frame": c.create_rectangle(0, 0, 1, 1, fill="#071519", outline=C_DIM, tags=t),
            "zone": c.create_rectangle(0, 0, 1, 1, outline=C_MID, dash=(4, 3), tags=t),
            "zone_label": c.create_text(0, 0, text="etkin alan", anchor="nw", fill="#2b525c", font=fb(8), tags=t),
            "meters": [], "meter_fill": [], "bones": [], "tips": [], "cursors": [],
            "status": c.create_text(0, 0, text="", anchor="nw", fill=C_TEXT, font=fb(9), tags=t),
            "hint": c.create_text(0, 0, text="", anchor="nw", fill=C_PRI, font=fb(9, True), tags=t),
            "hover": c.create_oval(0, 0, 1, 1, outline=C_GOLD, width=2, dash=(5, 3), tags=t, state="hidden"),
            "zoom_line": c.create_line(0, 0, 0, 0, fill=C_GOLD, width=1, dash=(6, 4), tags=t, state="hidden"),
        }
        for _ in range(2):
            items["meters"].append(c.create_rectangle(0, 0, 1, 1, fill="#091c20", outline=C_DIM, tags=t))
            items["meter_fill"].append(c.create_rectangle(0, 0, 1, 1, fill=C_MID, outline="", tags=t,
                                                          state="hidden"))
            items["bones"].append([c.create_line(0, 0, 0, 0, fill=C_MID, width=2, tags=t, state="hidden")
                                   for _ in HAND_CONNECTIONS])
            items["tips"].append([c.create_oval(0, 0, 1, 1, fill=C_TEXT, outline="", tags=t, state="hidden")
                                  for _ in range(2)])
            items["cursors"].append({
                "ring": c.create_oval(0, 0, 1, 1, outline=C_TEXT, width=2, tags=t, state="hidden"),
                "dot": c.create_oval(0, 0, 1, 1, fill=C_TEXT, outline="", tags=t, state="hidden"),
                "arc": c.create_arc(0, 0, 1, 1, start=90, extent=0, style="arc", outline=C_ORG, width=3,
                                    tags=t, state="hidden"),
                "text": c.create_text(0, 0, text="", anchor="w", fill=C_TEXT, font=fb(9), tags=t,
                                      state="hidden")})
        self.hud_items = items
        self.hover_id = None
        self._place_hud()

    def _place_hud(self):
        """Sol altta: kamera görünümü (etkin alan çerçevesiyle), sıkıştırma göstergeleri, kalite, ipucu."""
        if not self.hud_items:
            return
        c, it = self.canvas, self.hud_items
        h = max(320, c.winfo_height())
        pw, ph = 330, 284
        x0, y0 = 14, h - ph - 14
        c.coords(it["panel"], x0, y0, x0 + pw, y0 + ph)
        c.coords(it["title"], x0 + 10, y0 + 8)
        c.coords(it["quality"], x0 + pw - 10, y0 + 8)
        for k, bar in enumerate(it["qbars"]):
            bx = x0 + pw - 118 + k * 12
            c.coords(bar, bx, y0 + 10, bx + 9, y0 + 20)
        bw, bh = 256, 192                         # 4:3 kamera görünümü
        bx, by = x0 + 10, y0 + 30
        self.hud_box = (bx, by, bw, bh)
        c.coords(it["frame"], bx, by, bx + bw, by + bh)
        a = self.engine.p.amp
        lo, hi = 0.5 - 0.5 / a, 0.5 + 0.5 / a
        c.coords(it["zone"], bx + lo * bw, by + lo * bh, bx + hi * bw, by + hi * bh)
        c.coords(it["zone_label"], bx + lo * bw + 3, by + lo * bh + 2)
        for k in range(2):
            mx = bx + bw + 12 + k * 22
            c.coords(it["meters"][k], mx, by, mx + 14, by + bh)
        self.hud_meter_box = (bx + bw + 12, by, 14, bh)
        c.coords(it["status"], x0 + 10, y0 + 30 + bh + 8)
        c.coords(it["hint"], x0 + 10, y0 + 30 + bh + 28)
        c.itemconfigure(it["hint"], width=pw - 20)
        c.tag_raise("hud")

    def _hand_quality(self, result: dict, now: float) -> tuple[str, str, list[str]]:
        """(etiket, renk, uyarılar): uzaklık, kadraj kenarı, etkin alan, güven, ışık ve takip hızı."""
        warn: list[str] = []
        stats = self.hand_stats or {}
        hands = result["hands"]
        for h in hands:
            if h.get("span", 1) < 0.085:
                warn.append("elin uzak, biraz yaklaş")
            elif h.get("span", 0) > 0.42:
                warn.append("elin çok yakın, biraz uzaklaş")
            if h.get("edge", 1) < 0.02:
                warn.append("elin kadrajın kenarında")
            elif not h.get("in_zone", True):
                warn.append("etkin alanın dışındasın")
            if h.get("score", 1) < 0.75:
                warn.append("algılama güveni düşük")
        b = stats.get("brightness")
        if isinstance(b, (int, float)) and 0 <= b < 45:
            warn.append("ortam karanlık")
        fps = stats.get("fps")
        if hands and isinstance(fps, (int, float)) and 0 < fps < 15 and not stats.get("idle"):
            warn.append(f"takip yavaş ({fps:.0f} FPS)")
        warn = list(dict.fromkeys(warn))
        if not hands:
            return "EL YOK", C_MID, warn
        if not warn:
            return "İYİ", C_GREEN, warn
        return ("ORTA", C_GOLD, warn) if len(warn) == 1 else ("ZAYIF", C_RED, warn)

    def _hand_hint(self, result: dict, hover_label: str) -> str:
        hands = result["hands"]
        mode = result["mode"]
        if not hands:
            return "Elini kameraya göster; kesikli çerçevenin içinde tut."
        prog = result.get("reset_progress") or 0.0
        if prog > 0.05:
            return f"✌ Sıfırlanıyor… elini sabit tut  %{int(prog * 100)}"
        if any(not h["armed"] for h in hands) and mode == "idle":
            return "Elini bir an sabit tut…"
        if mode == "zoom":
            return f"Yakınlaştırma {result['zoom']:.2f}× · bitirmek için parmaklarını aç"
        if mode == "press":
            grabbed = [g[0] for g in self.hand_ctrl.grab.values() if g[0] in self.nodes]
            if grabbed:
                return f"‘{self.nodes[grabbed[0]]['label'][:28]}’ taşınıyor · bırakmak için parmaklarını aç"
            if self.hand_ctrl._suppressed:
                return "Odaklanıldı · parmaklarını aç"
            if self.hand_ctrl.rot_last:
                return "Grafik döndürülüyor · bırakmak için parmaklarını aç"
            return "Grafik kaydırılıyor · bırakmak için parmaklarını aç"
        if any(h.get("fist_pan") for h in hands):
            return "Yumrukla kaydırılıyor · dosyalara dokunulmaz · bitirmek için elini aç"
        if any(h.get("blocked") for h in hands):
            return "Yeni işlem için önce parmaklarını aç"
        if any(h.get("fist_pending") for h in hands):
            return "Yumruğu gezdir = kaydır · bir kez daha yumruk yap = yakınlaştırmadan çık"
        if hover_label:
            return f"‘{hover_label[:28]}’ · tutmak için parmaklarını birleştir · odak için iki kez"
        if self.view.mode3d:
            return "Boşlukta sıkıştır = döndür · ✊ gezdir = kaydır · iki elle = yakınlaştır · ✌ 1 sn = sıfırla"
        return "Sıkıştır = kaydır · ✊ gezdir = dokunmadan kaydır · iki elle = yakınlaştır · ✌ 1 sn = sıfırla"

    def _draw_hud(self, result: dict):
        if not self.hud_items:
            return
        c, it = self.canvas, self.hud_items
        bx, by, bw, bh = self.hud_box
        mx0, my0, mw, mh = self.hud_meter_box
        hands = {h["slot"]: h for h in result["hands"]}
        for slot in (0, 1):
            h = hands.get(slot)
            bones, tips, cur = it["bones"][slot], it["tips"][slot], it["cursors"][slot]
            fill = it["meter_fill"][slot]
            if not h or not h.get("landmarks"):
                for item in (*bones, *tips, fill, *cur.values()):
                    c.itemconfigure(item, state="hidden")
                continue
            lm = h["landmarks"]
            col = C_GOLD if h["pinching"] else (C_TEXT if h["armed"] else "#364c52")
            for b, (i, j) in zip(bones, HAND_CONNECTIONS):
                c.coords(b, bx + lm[i][0] * bw, by + lm[i][1] * bh, bx + lm[j][0] * bw, by + lm[j][1] * bh)
                c.itemconfigure(b, state="normal", fill=col)
            for dot, k in zip(tips, (4, 8)):          # başparmak ve işaret parmağı uçları
                px, py = bx + lm[k][0] * bw, by + lm[k][1] * bh
                c.coords(dot, px - 3.5, py - 3.5, px + 3.5, py + 3.5)
                c.itemconfigure(dot, state="normal", fill=C_GOLD if h["pinching"] else C_PRI)
            # sıkıştırma yakınlık göstergesi (dikey çubuk): dolunca tutuş başlar
            close = float(h.get("closeness", 0.0))
            x = mx0 + slot * 22
            c.coords(fill, x + 2, my0 + mh - 2 - (mh - 4) * close, x + mw - 2, my0 + mh - 2)
            c.itemconfigure(fill, state="normal", fill=C_GOLD if h["pinching"] else (C_PRI if close > 0.6 else C_MID))
            # grafikteki imleç: halka parmaklar yaklaştıkça küçülür, sıkıştırınca altın
            sx, sy = self.hand_ctrl.to_screen(*h["cursor"])
            r = 10.0 if h["pinching"] else 18.0 - 7.0 * close
            c.coords(cur["ring"], sx - r, sy - r, sx + r, sy + r)
            c.itemconfigure(cur["ring"], state="normal", outline=col, width=3 if h["pinching"] else 2)
            c.coords(cur["dot"], sx - 3, sy - 3, sx + 3, sy + 3)
            c.itemconfigure(cur["dot"], state="normal", fill=col)
            c.coords(cur["text"], sx + r + 7, sy)
            c.itemconfigure(cur["text"], state="normal", text=h["state"], fill=col)
            c.itemconfigure(cur["arc"], state="hidden")
        # hedeflenen düğüm: sıkıştırınca tutulacak olan önceden vurgulanır
        hover_id, hover_label = None, ""
        if result["mode"] == "idle":
            for h in result["hands"]:
                if h["armed"] and not h.get("blocked") and h.get("pose") != "fist" and not h.get("fist_pan"):
                    hover_id = self.hand_ctrl.hover(*h["cursor"])   # yumruk dosya tutmaz → vurgulanmaz
                    if hover_id:
                        break
        elif result["mode"] == "press":
            grabbed = [g[0] for g in self.hand_ctrl.grab.values()]
            hover_id = grabbed[0] if grabbed else None
        if hover_id and hover_id in self.nodes and hover_id in self.view.pos:
            nx, ny = self.view.node_screen(hover_id)
            rr = self.view.screen_radius(hover_id) + 8
            c.coords(it["hover"], nx - rr, ny - rr, nx + rr, ny + rr)
            c.itemconfigure(it["hover"], state="normal",
                            outline=C_GOLD if result["mode"] == "press" else C_TEXT)
            hover_label = self.nodes[hover_id]["label"]
        else:
            c.itemconfigure(it["hover"], state="hidden")
        self.hover_id = hover_id
        # iki elle yakınlaştırma: iki imleç arasında çizgi
        if result["mode"] == "zoom" and len(hands) == 2:
            (ax, ay), (bx2, by2) = (self.hand_ctrl.to_screen(*hands[k]["cursor"]) for k in (0, 1))
            c.coords(it["zoom_line"], ax, ay, bx2, by2)
            c.itemconfigure(it["zoom_line"], state="normal")
        else:
            c.itemconfigure(it["zoom_line"], state="hidden")
        # kalite ve durum
        label, qcol, warn = self._hand_quality(result, time.monotonic())
        c.itemconfigure(it["quality"], text=label, fill=qcol)
        level = {"İYİ": 3, "ORTA": 2, "ZAYIF": 1}.get(label, 0)
        for k, bar in enumerate(it["qbars"]):
            c.itemconfigure(bar, fill=qcol if k < level else C_DIM)
        n = len(result["hands"])
        status = (f"{n} el görünüyor" if n else "El görünmüyor")
        fps = (self.hand_stats or {}).get("fps")
        if isinstance(fps, (int, float)) and fps > 0:
            status += f" · {fps:.0f} FPS"
        if warn:
            status = "⚠ " + " · ".join(warn[:2])
        c.itemconfigure(it["status"], text=status, fill=C_GOLD if warn else C_TEXT)
        c.itemconfigure(it["hint"], text=self._hand_hint(result, hover_label))
        self.hand_quality = (label, warn)
        c.tag_raise("hud")

    def _on_unmap(self, e):
        if e.widget is self.root and self._spin_job:
            self.root.after_cancel(self._spin_job)   # gizliyken döndürme işlemci harcamasın
            self._spin_job = None
        if e.widget is self.root and self.tracker.running and not self.tracker.paused:
            self.tracker.pause()   # simge durumunda kamera kapanır
            self._unmap_paused = True
            _hlog("pencere gizlendi; kamera duraklatıldı")

    def _on_map(self, e):
        # Yalnızca gizlenme yüzünden duraklatıldıysa kendiliğinden sürdür (5 dk boşta kalma
        # duraklatması kullanıcının tıklamasını bekler). Eskiden durum "paused"a döndüğü için
        # pencere geri açılınca kamera hiç sürmüyordu.
        if e.widget is self.root and self.spin and not self._spin_job:
            self._spin_job = self.root.after(40, self._spin_step)
        if e.widget is self.root and self._unmap_paused:
            self._unmap_paused = False
            if self.tracker.running:
                self.tracker.resume()
                self.hand_state = "active"
                self.hand_last_seen = time.monotonic()
                self.hand_last_frame = time.monotonic()
                self._refresh_hand_button()
                self._update_footer()
                _hlog("pencere göründü; kamera sürdürüldü")

    # ── kapanış ──────────────────────────────────────────────────────────
    def close(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        for job in (self._spin_job, self._frame_job, self._depth_job, self._anim_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
        try:
            self.tracker.stop()
        except Exception:
            pass
        if self._index_proc is not None:
            pass  # indeksleyici kendi başına bitsin; kilit dosyası ile korunur
        emit({"event": "closed"})
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus", default="")
    ap.add_argument("--ipc", action="store_true")
    args = ap.parse_args(argv)
    root = tk.Tk()
    root.title("JARVIS — İkinci Beyin")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    # Menü çubuğu ve Dock'un altına taşmasın: görünür alanın içinde kal.
    w, h = int(sw * 0.92), max(560, sh - 160)
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+34")
    root.minsize(980, 620)
    try:
        from PIL import Image, ImageTk
        with Image.open(BASE_DIR / "Icon" / "jarvis_logo.png") as logo:
            icon = ImageTk.PhotoImage(logo.convert("RGBA").resize((256, 256)), master=root)
        root.iconphoto(True, icon)
        root._jarvis_icon = icon
    except Exception:
        pass
    app = BrainApp(root, focus=args.focus, ipc=args.ipc)
    root.after(50, app.raise_window)
    try:
        root.mainloop()
    finally:
        app.tracker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
