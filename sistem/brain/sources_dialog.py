"""Ayarlar › İKİNCİ BEYİN · KAYNAKLAR penceresi (ana süreçte küçük bir Toplevel).

JARVIS yalnızca burada seçilen klasörleri tarar. Klasör eklemek/kaldırmak
kullanıcının açık eylemiyle olur.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from brain import query, settings

BG, PANEL, PRI, MID, DIM, TEXT, GOLD, RED, BLUE = ("#08191d", "#091b1f", "#73c1cc", "#2f6f7d", "#123540",
                                                   "#d7eff4", "#d6c052", "#ff3344", "#8fb6ff")


def _btn(parent, text, command, color=PRI):
    lbl = tk.Label(parent, text=text, fg=color, bg="#0c252b", font=("Grift", 10, "bold"), cursor="hand2",
                   padx=10, pady=6, highlightthickness=1, highlightbackground=color)
    lbl.bind("<Button-1>", lambda e: command())
    return lbl


class SourcesDialog:
    def __init__(self, parent, on_reindex, on_open_graph, on_changed=None):
        self.on_reindex, self.on_open_graph, self.on_changed = on_reindex, on_open_graph, on_changed
        self.win = tk.Toplevel(parent)
        self.win.title("JARVIS — İkinci Beyin kaynakları")
        try:
            self.win.transient(parent)  # tam ekran JARVIS'in üstünde küçük pencere olarak kalsın
        except tk.TclError:
            pass
        sw, sh = parent.winfo_screenwidth(), parent.winfo_screenheight()
        self.win.geometry(f"620x470+{max(0, (sw - 620) // 2)}+{max(40, (sh - 470) // 3)}")
        self.win.minsize(520, 400)
        self.win.configure(bg=BG)
        tk.Label(self.win, text="İKİNCİ BEYİN · KAYNAK KLASÖRLER", fg=PRI, bg=BG,
                 font=("Grift Extra Bold", 14)).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(self.win, justify="left", wraplength=570, fg="#7aa8b3", bg=BG, font=("Grift", 10),
                 text=("JARVIS yalnızca burada seçtiğin klasörleri tarar; bilgisayarın geri kalanına bakmaz. "
                       "Tarama ve indeks bu Mac'te kalır. Notların hakkında soru sorduğunda yalnızca cevap "
                       "için gereken kısa alıntılar sesli asistana (Gemini) gönderilir. İndeks: sistem/memory/"
                       "second_brain.sqlite3")).pack(anchor="w", padx=18)
        frame = tk.Frame(self.win, bg=BG)
        frame.pack(fill="both", expand=True, padx=18, pady=10)
        self.listbox = tk.Listbox(frame, bg="#07161a", fg=TEXT, selectbackground=MID, selectforeground=TEXT,
                                  highlightthickness=1, highlightbackground=DIM, relief="flat",
                                  font=("Grift", 11), activestyle="none")
        self.listbox.pack(fill="both", expand=True)
        row = tk.Frame(self.win, bg=BG)
        row.pack(fill="x", padx=18)
        _btn(row, "+ KLASÖR EKLE", self.add, BLUE).pack(side="left", padx=(0, 8))
        _btn(row, "− KALDIR", self.remove, RED).pack(side="left", padx=(0, 8))
        _btn(row, "↻ ŞİMDİ TARA", self.reindex).pack(side="left", padx=(0, 8))
        _btn(row, "◈ GRAFİĞİ AÇ", self.open_graph, GOLD).pack(side="left")
        _btn(row, "KAPAT", self.win.destroy, MID).pack(side="right")
        self.status = tk.Label(self.win, text="", fg=GOLD, bg=BG, font=("Grift", 10), justify="left",
                               wraplength=570, anchor="w")
        self.status.pack(fill="x", padx=18, pady=(10, 14))
        self.refresh()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def refresh(self, message: str = ""):
        if not self.alive():
            return
        self.listbox.delete(0, "end")
        sources = settings.get_sources()
        for s in sources:
            self.listbox.insert("end", "  " + s.replace(str(Path.home()), "~"))
        if not sources:
            self.listbox.insert("end", "  (henüz klasör yok — ‘+ KLASÖR EKLE’)")
        st = query.status()
        if st["indexing"]:
            info = "Tarama sürüyor…"
        elif st["indexed"]:
            info = (f"Son tarama {st['built_at']} · {st['nodes']} düğüm · {st['explicit_edges']} açık / "
                    f"{st['inferred_edges']} tahmini bağlantı")
            if st["stale_sources"]:
                info += " · kaynaklar değişti, yeniden tarama gerekli"
        else:
            info = "Henüz indeks yok."
        self.status.configure(text=(message + "\n" if message else "") + info)

    def add(self):
        path = filedialog.askdirectory(parent=self.win, title="Not veya proje klasörü seç", mustexist=True)
        if not path:
            return
        ok, message, _ = settings.add_source(path)
        self.refresh(message)
        if ok:
            if self.on_changed:
                self.on_changed()
            self.reindex()

    def remove(self):
        sel = self.listbox.curselection()
        sources = settings.get_sources()
        if not sel or sel[0] >= len(sources):
            self.refresh("Kaldırmak için listeden bir klasör seç.")
            return
        settings.remove_source(sources[sel[0]])
        if self.on_changed:
            self.on_changed()
        self.refresh("Klasör kaldırıldı; yeniden tarama başlatıldı (indeksten de çıkacak).")
        if settings.get_sources():
            self.on_reindex()

    def reindex(self):
        result = self.on_reindex()
        text = {"started": "Tarama başladı (arka planda, düşük öncelik).",
                "window": "Tarama İkinci Beyin penceresinde başladı.",
                "busy": "Zaten bir tarama sürüyor.",
                "needs_setup": "Önce bir klasör ekle."}.get(result, str(result))
        self.refresh(text)
        self.win.after(2500, self.refresh)

    def open_graph(self):
        self.on_open_graph()
