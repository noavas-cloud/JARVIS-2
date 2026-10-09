"""JARVIS Chrome penceresi: görünmez Chrome'un (actions/chrome_cdp.py) canlı görüntüsü + kullanıcı girdisi.

Chrome'un kendisi hiç görünmez (görünür Chrome simge durumunda/gizliyken sayfayı çizmiyor, tıklama ulaşmıyor). Kullanıcı
küreye tıklayınca bu pencere açılır: ~3 kare/sn ekran görüntüsü gösterilir; pencereye tıklama, tekerlek, klavye
(⌘V yapıştırma dahil) Chrome'a iletilir — kullanıcı robot doğrulamasını geçebilir, bir siteye kendisi giriş yapabilir.
Ajan yardım isterse (ask_user) üstte sarı şerit + BİTTİ / ATLA çıkar. KAPAT ya da küreye yeniden tıklama pencereyi kapatır.
Pencere ana iş parçacığında (Tk) çalışır; çekirdeğe yalnız core._call / loop.call_soon_threadsafe ile gider.
"""

from __future__ import annotations

import io
import threading
import tkinter as tk

from PIL import Image, ImageTk

from jarvis.ui import theme as T

KEYSYMS = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Tab": "Tab", "Escape": "Escape",
           "Up": "ArrowUp", "Down": "ArrowDown", "Left": "ArrowLeft", "Right": "ArrowRight", "Delete": "Delete",
           "Prior": "PageUp", "Next": "PageDown", "Home": "Home", "End": "End"}
COMMAND_MASK = 0x8          # macOS Tk: ⌘ tuşu


class BrowserView:
    def __init__(self, root, core):
        self.root, self.core = root, core
        self.win: tk.Toplevel | None = None
        self.seen = -1
        self.photo = None
        self.box = (0, 0, 1, 1)         # görüntünün tuvaldeki yeri (x, y, genişlik, yükseklik)
        self._help_shown = None

    @property
    def host(self):
        return self.core.research

    # ── Her karede (window._animate) ─────────────────────────────────────────────────────────
    def sync(self):
        want = bool(self.core.state.browser_visible)
        if want and self.win is None:
            self._open()
        elif not want and self.win is not None:
            self._destroy()
        if self.win is None:
            return
        self._update_bars()
        host = self.host
        if host.frame and host.frame_id != self.seen:
            self.seen = host.frame_id
            self._show(host.frame)

    # ── Pencere ──────────────────────────────────────────────────────────────────────────────
    def _open(self):
        w = self.win = tk.Toplevel(self.root, bg=T.BG)
        w.title("JARVIS Chrome")
        w.geometry("1060x800")
        w.minsize(640, 480)
        w.protocol("WM_DELETE_WINDOW", self.close)
        top = tk.Frame(w, bg=T.PANEL, height=44)
        top.pack(fill="x")
        tk.Label(top, text="JARVIS CHROME", bg=T.PANEL, fg=T.CYAN, font=T.bold(11)).pack(side="left", padx=(14, 10))
        self.status = tk.Label(top, text="", bg=T.PANEL, fg=T.TEXT_DIM, font=T.body(11), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        close = tk.Label(top, text="KAPAT", bg=T.PANEL_HI, fg=T.TEXT, font=T.bold(10), padx=14, pady=6, cursor="hand2")
        close.pack(side="right", padx=10, pady=6)
        close.bind("<Button-1>", lambda e: self.close())
        # Araştırma bitince görünür (3. parça)
        self.report_btn = tk.Label(top, text="RAPORU AÇ", bg=T.CYAN, fg=T.BG, font=T.bold(10), padx=14, pady=6,
                                   cursor="hand2")
        self.report_btn.bind("<Button-1>", lambda e: threading.Thread(target=self.host.open_report, daemon=True).start())
        self._report_shown = False
        self.help = tk.Frame(w, bg="#3a3210")
        self.help_text = tk.Label(self.help, text="", bg="#3a3210", fg="#ffe9a6", font=T.body(12), anchor="w",
                                  justify="left", wraplength=760)
        self.help_text.pack(side="left", fill="x", expand=True, padx=14, pady=8)
        for label, done in (("ATLA", False), ("BİTTİ", True)):
            b = tk.Label(self.help, text=label, bg=T.GOLD if done else T.PANEL_HI, fg=T.BG if done else T.TEXT,
                         font=T.bold(10), padx=14, pady=6, cursor="hand2")
            b.pack(side="right", padx=(0, 10), pady=6)
            b.bind("<Button-1>", lambda e, d=done: self._answer(d))
        # Sağda: ajanın açtığı sayfalar + not sayısı (3. parça)
        side = tk.Frame(w, bg=T.PANEL, width=250)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="KAYNAKLAR", bg=T.PANEL, fg=T.CYAN, font=T.bold(10), anchor="w").pack(
            fill="x", padx=12, pady=(12, 4))
        self.sources = tk.Label(side, text="Henüz sayfa açılmadı.", bg=T.PANEL, fg=T.TEXT_DIM, font=T.body(10),
                                anchor="nw", justify="left", wraplength=226)
        self.sources.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas = tk.Canvas(w, bg="#05090b", highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(20, 20, text="Görüntü bekleniyor…", fill=T.TEXT_DIM, font=T.body(12), anchor="nw",
                                tags="wait")
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        w.bind("<Key>", self._key)
        self.seen = -1
        self._help_shown = None
        self._last = None
        w.focus_force()

    def close(self):
        """KAPAT / pencere düğmesi: yalnız pencere kapanır (araştırma sürer); araştırma bittiyse Chrome da kapanır."""
        self.core.state.browser_visible = False
        self.core._call(self.host.set_visible(False))
        self._destroy()

    def _destroy(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None
        self.photo = None

    def _update_bars(self):
        host = self.host
        job = host.job
        if job is None:
            text = ""
        elif job.status == "running":
            text = f"{job.activity}  ·  adım {job.step}/{job.max_steps}  ·  {job.question[:70]}"
        else:
            text = {"done": "Araştırma bitti", "cancelled": "Araştırma durdu"}.get(job.status, "Araştırma yarıda kaldı") \
                + "  ·  pencereyi kapatınca Chrome da kapanır"
        if self.status.cget("text") != text:
            self.status.configure(text=text)
        if job is not None:
            lines = [f"{i}. {(t or u)[:60]}" for i, (t, u) in enumerate(job.visited[-14:], start=max(1, len(job.visited) - 13))]
            src = ("\n\n".join(lines) if lines else "Henüz sayfa açılmadı.") + f"\n\n— {len(job.notes)} bulgu not edildi"
            if self.sources.cget("text") != src:
                self.sources.configure(text=src)
            show = job.status != "running" and bool(job.report_path)
            if show != self._report_shown:
                self._report_shown = show
                if show:
                    self.report_btn.pack(side="right", padx=(0, 4), pady=6)
                else:
                    self.report_btn.pack_forget()
        reason = host.help[0] if host.help else None
        if reason != self._help_shown:
            self._help_shown = reason
            if reason:
                self.help_text.configure(text=f"JARVIS yardım istiyor: {reason}\nAşağıda gerekeni yap, sonra BİTTİ'ye bas.")
                self.help.pack(fill="x", after=self.win.winfo_children()[0])
                self.win.deiconify()
                self.win.lift()
            else:
                self.help.pack_forget()

    def _answer(self, done: bool):
        loop = self.core.loop
        if loop is not None:
            loop.call_soon_threadsafe(self.host.answer_help, done)

    # ── Görüntü ──────────────────────────────────────────────────────────────────────────────
    def _show(self, data: bytes):
        try:
            self._last = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            return
        self._redraw()

    def _redraw(self):
        img = getattr(self, "_last", None)
        if img is None or self.win is None:
            return
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = min(cw / img.width, ch / img.height)
        w, h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        x, y = (cw - w) // 2, (ch - h) // 2
        self.photo = ImageTk.PhotoImage(img.resize((w, h), Image.BILINEAR))
        self.canvas.delete("all")
        self.canvas.create_image(x, y, image=self.photo, anchor="nw")
        self.box = (x, y, w, h)

    # ── Kullanıcı girdisi → Chrome ───────────────────────────────────────────────────────────
    def _click(self, e):
        x, y, w, h = self.box
        if not (x <= e.x <= x + w and y <= e.y <= y + h):
            return
        self.win.focus_set()
        self.core._call(self.host.viewer_click((e.x - x) / w, (e.y - y) / h))

    def _wheel(self, e):
        step = -e.delta / 120.0 if abs(e.delta) >= 120 else -e.delta / 4.0
        if step:
            self.core._call(self.host.viewer_scroll(max(-2.0, min(2.0, step * 0.4))))

    def _key(self, e):
        if e.state & COMMAND_MASK:
            if e.keysym.lower() == "v":
                try:
                    text = self.root.clipboard_get()
                except tk.TclError:
                    return
                self.core._call(self.host.viewer_text(text[:4000]))
            return
        if e.keysym in KEYSYMS:
            self.core._call(self.host.viewer_key(KEYSYMS[e.keysym]))
        elif e.char and e.char >= " ":
            self.core._call(self.host.viewer_text(e.char))
