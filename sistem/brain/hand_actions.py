"""El hareketi olaylarını grafik görünümüne uygular.

Yalnızca grafiği yönetir: düğüm tutma/taşıma, kaydırma (3B'de döndürme), yakınlaştırma,
sıfırlama, tutulan düğümü seçme (ayrıntı paneli), çift sıkıştırmayla bir düğüme odaklanma ve çift
yumrukla yakınlaştırmadan çıkma (pencere uygular), yumrukla kaydırma (düğüm tutmadan; 2B ve 3B'de). Dosya açma, tıklama veya Mac'te başka
hiçbir işlem yapmaz.
"""

from __future__ import annotations

import math
import time

from brain.view import GraphView


HOVER_SLACK_PX = 26.0   # imleç düğümün bu kadar yakınındaysa o düğüm hedeflenir (vurgulanır, tutulur)
TAP_MAX_S = 0.6         # "dokunma": kısa sıkıştır-bırak…
TAP_MOVE_PX = 30.0      # …ve bu kadardan az kıpırdama
DOUBLE_TAP_S = 0.9      # iki dokunma arası en fazla (çift sıkıştırma = çift tıklama)


class HandGraphController:
    def __init__(self, view: GraphView):
        self.view = view
        self.grab: dict[int, tuple[str, float, float]] = {}
        self.pan_last: dict[int, tuple[float, float]] = {}
        self.rot_last: dict[int, tuple[float, float]] = {}
        self.status = ""
        self._tap_start: dict[int, tuple[str | None, float, float, float]] = {}
        self._last_tap: tuple[str | None, float, float, float] | None = None
        self._suppressed: set[int] = set()   # çift sıkıştırmanın ikinci tutuşu: taşıma/döndürme yapmaz

    def to_screen(self, nx: float, ny: float) -> tuple[float, float]:
        return nx * self.view.width, ny * self.view.height

    def hover(self, nx: float, ny: float) -> str | None:
        """İmlecin şu an hedeflediği düğüm: sıkıştırınca tutulacak olan (ekranda önceden vurgulanır)."""
        sx, sy = self.to_screen(nx, ny)
        return self.view.hit_test(sx, sy, slack_px=HOVER_SLACK_PX)

    def _is_double(self, nid: str | None, sx: float, sy: float, now: float) -> bool:
        last = self._last_tap
        if last is None or now - last[3] > DOUBLE_TAP_S:
            return False
        if nid is not None or last[0] is not None:
            return nid == last[0]
        return math.hypot(sx - last[1], sy - last[2]) <= 3 * TAP_MOVE_PX   # boşlukta iki kez

    def apply(self, events: list[tuple], now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        changes = {"moved": set(), "pan": [0.0, 0.0], "zoom": [], "reset": False,
                   "selected": None, "released": [], "rotate": False, "double": None}
        for ev in events:
            kind = ev[0]
            if kind == "press":
                _, slot, nx, ny = ev
                sx, sy = self.to_screen(nx, ny)
                nid = self.view.hit_test(sx, sy, slack_px=HOVER_SLACK_PX)
                if self._is_double(nid, sx, sy, now):
                    # Çift sıkıştırma: bu tutuş düğümü taşımaz; pencere odaklanır (boşlukta: odaktan çık).
                    changes["double"] = nid or ""
                    self._last_tap = None
                    self._suppressed.add(slot)
                    self.status = "ODAK"
                    continue
                self._tap_start[slot] = (nid, sx, sy, now)
                if nid:
                    x, y = self.view.node_screen(nid)
                    self.grab[slot] = (nid, x - sx, y - sy)
                    changes["selected"] = nid
                    self.status = "TUTULUYOR"
                elif self.view.mode3d:
                    # 3B: boşlukta sıkıştırıp sürüklemek grafiği döndürür.
                    self.rot_last[slot] = (sx, sy)
                    self.status = "DÖNDÜRME"
                else:
                    self.pan_last[slot] = (sx, sy)
                    self.status = "KAYDIRMA"
            elif kind == "drag":
                _, slot, nx, ny = ev
                sx, sy = self.to_screen(nx, ny)
                if slot in self._suppressed:
                    continue
                if slot in self.grab:
                    nid, ox, oy = self.grab[slot]
                    self.view.move_node_screen(nid, sx + ox, sy + oy)
                    changes["moved"].add(nid)
                elif slot in self.rot_last:
                    lx, ly = self.rot_last[slot]
                    k = math.pi / max(1.0, self.view.width)     # ekran genişliği boyunca ≈ 180°
                    if self.view.rotate(-(sx - lx) * k, (sy - ly) * k):
                        changes["rotate"] = True
                    self.rot_last[slot] = (sx, sy)
                elif slot in self.pan_last:
                    lx, ly = self.pan_last[slot]
                    dx, dy = self.view.pan(sx - lx, sy - ly)
                    changes["pan"][0] += dx
                    changes["pan"][1] += dy
                    self.pan_last[slot] = (sx, sy)
            elif kind == "release":
                slot = ev[1]
                reason = ev[4] if len(ev) > 4 else "open"
                if slot in self.grab:
                    changes["released"].append(self.grab.pop(slot)[0])
                self.pan_last.pop(slot, None)
                self.rot_last.pop(slot, None)
                start = self._tap_start.pop(slot, None)
                if slot in self._suppressed:
                    self._suppressed.discard(slot)
                elif start is not None and reason == "open":
                    sx, sy = self.to_screen(ev[2], ev[3])
                    quick = now - start[3] <= TAP_MAX_S
                    still = math.hypot(sx - start[1], sy - start[2]) <= TAP_MOVE_PX
                    self._last_tap = (start[0], sx, sy, now) if quick and still else None
                self.status = ""
            elif kind == "fist_pan_begin":
                # Yumrukla kaydırma: hiçbir düğüm tutulmaz/seçilmez, 3B'de de döndürmek yerine kaydırır.
                _, slot, nx, ny = ev
                self.pan_last[slot] = self.to_screen(nx, ny)
                self._last_tap = None
                self.status = "KAYDIRMA"
            elif kind == "fist_pan":
                _, slot, nx, ny = ev
                if slot in self.pan_last:
                    sx, sy = self.to_screen(nx, ny)
                    lx, ly = self.pan_last[slot]
                    dx, dy = self.view.pan(sx - lx, sy - ly)
                    changes["pan"][0] += dx
                    changes["pan"][1] += dy
                    self.pan_last[slot] = (sx, sy)
            elif kind == "fist_pan_end":
                self.pan_last.pop(ev[1], None)
                self.status = ""
            elif kind == "zoom_begin":
                self.grab.clear()
                self.pan_last.clear()
                self.rot_last.clear()
                self._tap_start.clear()
                self._last_tap = None
                self.status = "YAKINLAŞTIRMA"
            elif kind == "zoom":
                _, factor, nx, ny = ev
                sx, sy = self.to_screen(nx, ny)
                applied = self.view.zoom_at(factor, sx, sy)
                if applied != 1.0:
                    changes["zoom"].append((applied, sx, sy))
            elif kind == "zoom_end":
                self.status = ""
            elif kind == "reset":
                self.grab.clear()
                self.pan_last.clear()
                self.rot_last.clear()
                self.view.reset()
                changes["reset"] = True
                self.status = "SIFIRLANDI"
            elif kind == "unzoom":
                # Çift yumruk: yakınlaştırmadan çıkma pencerede yapılır (görünüm sıfırlanmaz).
                # Önceki bir dokunma, sonraki sıkıştırmayla çift sıkıştırma (odak) sayılmasın.
                self._last_tap = None
                self.status = "UZAKLAŞ"
            elif kind == "hand_lost":
                slot = ev[1]
                if slot in self.grab:
                    changes["released"].append(self.grab.pop(slot)[0])
                self.pan_last.pop(slot, None)
                self.rot_last.pop(slot, None)
                self._tap_start.pop(slot, None)
                self._suppressed.discard(slot)
        return changes
