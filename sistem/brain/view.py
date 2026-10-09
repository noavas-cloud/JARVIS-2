"""Grafik görünüm modeli: dünya↔ekran dönüşümü, yakınlaştırma, döndürme, isabet testi, sıfırlama.

Tk'den bağımsızdır; fare, klavye ve el hareketleri aynı yöntemleri çağırır.

3B görünüm: her düğümün (x, y) yerleşimine bir derinlik (z) eklenir. Kamera grafiğin bir
merkez noktası (pivot) etrafında döner (yaw = yatay, pitch = dikey) ve perspektifle
yansıtılır. Yansıtma sonucu (u, v) düzlemsel koordinattır; ekran = u·ölçek + kaydırma.
Böylece kaydırma ve yakınlaştırma 2B'deki gibi ucuz kalır; yalnızca döndürme yeniden
yansıtma gerektirir. 2B kipte (ya da açı 0 iken derinlik kullanılmaz) u = x, v = y'dir.
"""

from __future__ import annotations

import math

MIN_SCALE = 0.04
MAX_SCALE = 8.0
DEFAULT_YAW = math.radians(28.0)
DEFAULT_PITCH = math.radians(-22.0)
MAX_PITCH = math.radians(85.0)
CAMERA_FACTOR = 2.6          # kamera uzaklığı = grafik yarıçapı × bu değer (küçük = güçlü perspektif)


class GraphView:
    def __init__(self, positions: dict[str, tuple[float, float]], width: int = 1000, height: int = 700,
                 depths: dict[str, float] | None = None):
        self.base = dict(positions)          # indeksleyicinin yerleşimi (sıfırlamada dönülen)
        self.pos = dict(positions)           # mevcut (elle taşınmış olabilir)
        self.base_z = {nid: float((depths or {}).get(nid, 0.0)) for nid in positions}
        self.z = dict(self.base_z)
        self.width, self.height = max(1, width), max(1, height)
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self.visible: set[str] = set(positions)
        self.radius: dict[str, float] = {}
        self._grid: dict | None = None
        self._cell = 80.0
        # 3B kamera
        self.mode3d = False
        self.yaw = 0.0
        self.pitch = 0.0
        self.pivot = (0.0, 0.0, 0.0)
        self.cam_dist = 1000.0
        self.extent = 500.0
        self._proj: dict[str, tuple[float, float, float, float]] | None = None
        self._set_pivot_from(list(positions))

    # ── 3B yansıtma ───────────────────────────────────────────────────────
    @property
    def rotated(self) -> bool:
        return self.mode3d and (self.yaw != 0.0 or self.pitch != 0.0)

    def _set_pivot_from(self, ids):
        pts = [(self.pos[i][0], self.pos[i][1], self.z.get(i, 0.0)) for i in ids if i in self.pos]
        if not pts:
            self.pivot, self.extent = (0.0, 0.0, 0.0), 500.0
        else:
            xs, ys, zs = zip(*pts)
            cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
            self.pivot = (cx, cy, cz)
            r2 = max((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 for x, y, z in pts)
            self.extent = max(40.0, math.sqrt(r2))
        self.cam_dist = self.extent * CAMERA_FACTOR
        self.invalidate()

    def invalidate(self):
        """Döndürme, pivot, kip ya da düğüm konumu değişince yansıtma yeniden hesaplanır."""
        self._proj = None
        self._grid = None

    def _project_one(self, x: float, y: float, z: float, cos_y, sin_y, cos_p, sin_p):
        cx, cy, cz = self.pivot
        dx, dy = x - cx, y - cy
        dz = (z - cz) if self.mode3d else 0.0
        x1 = dx * cos_y + dz * sin_y
        z1 = -dx * sin_y + dz * cos_y
        y2 = dy * cos_p - z1 * sin_p
        z2 = dy * sin_p + z1 * cos_p
        if self.mode3d:
            d = self.cam_dist
            persp = d / max(0.2 * d, d + z2)
        else:
            persp = 1.0
        return cx + x1 * persp, cy + y2 * persp, persp, z2

    def _angles(self):
        yaw, pitch = (self.yaw, self.pitch) if self.mode3d else (0.0, 0.0)
        return math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)

    def _ensure_proj(self) -> dict:
        if self._proj is None:
            cy_, sy_, cp_, sp_ = self._angles()
            proj = {}
            z = self.z
            for nid, (x, y) in self.pos.items():
                proj[nid] = self._project_one(x, y, z.get(nid, 0.0), cy_, sy_, cp_, sp_)
            self._proj = proj
        return self._proj

    def project(self, nid: str) -> tuple[float, float, float, float]:
        """(u, v, perspektif çarpanı, derinlik) — derinlik büyüdükçe düğüm uzaktadır."""
        proj = self._ensure_proj()
        if nid not in proj and nid in self.pos:
            x, y = self.pos[nid]
            proj[nid] = self._project_one(x, y, self.z.get(nid, 0.0), *self._angles())
        return proj[nid]

    def depth01(self, nid: str) -> float:
        """0 = en yakın, 1 = en uzak (gölgelendirme ve çizim sırası için)."""
        if not self.mode3d:
            return 0.5
        depth = self.project(nid)[3]
        return min(1.0, max(0.0, 0.5 + depth / (2.0 * self.extent)))

    def set_mode3d(self, on: bool):
        on = bool(on)
        if on == self.mode3d:
            return
        self.mode3d = on
        if on and self.yaw == 0.0 and self.pitch == 0.0:
            self.yaw, self.pitch = DEFAULT_YAW, DEFAULT_PITCH
        self.invalidate()

    def rotate(self, dyaw: float, dpitch: float) -> bool:
        """Kamerayı pivot etrafında döndürür (yalnızca 3B kipte). Değişiklik olduysa True."""
        if not self.mode3d or (not dyaw and not dpitch):
            return False
        if any(math.isnan(v) or math.isinf(v) for v in (dyaw, dpitch)):
            return False
        new_pitch = max(-MAX_PITCH, min(MAX_PITCH, self.pitch + dpitch))
        new_yaw = (self.yaw + dyaw + math.pi) % (2 * math.pi) - math.pi
        if new_pitch == self.pitch and new_yaw == self.yaw:
            return False
        self.yaw, self.pitch = new_yaw, new_pitch
        self.invalidate()
        return True

    # ── dönüşümler ────────────────────────────────────────────────────────
    def to_screen(self, u: float, v: float) -> tuple[float, float]:
        return u * self.scale + self.ox, v * self.scale + self.oy

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        """Ekran → yansıtma düzlemi (2B kipte doğrudan dünya koordinatı)."""
        return (sx - self.ox) / self.scale, (sy - self.oy) / self.scale

    def node_screen(self, nid: str) -> tuple[float, float]:
        u, v, _, _ = self.project(nid)
        return u * self.scale + self.ox, v * self.scale + self.oy

    def resize(self, width: int, height: int):
        self.width, self.height = max(1, width), max(1, height)

    # ── görünüm işlemleri (Tk için etkin dönüşümü döndürür) ───────────────
    def pan(self, dx: float, dy: float) -> tuple[float, float]:
        self.ox += dx
        self.oy += dy
        return dx, dy

    def zoom_at(self, factor: float, sx: float, sy: float) -> float:
        """Ekrandaki (sx, sy) noktası sabit kalacak biçimde ölçekler; uygulanan çarpanı döndürür."""
        if not factor or factor <= 0 or math.isnan(factor):
            return 1.0
        new = min(MAX_SCALE, max(MIN_SCALE, self.scale * factor))
        f = new / self.scale
        if abs(f - 1.0) < 1e-9:
            return 1.0
        self.ox = sx - (sx - self.ox) * f
        self.oy = sy - (sy - self.oy) * f
        self.scale = new
        return f

    def bounds(self, ids=None) -> tuple[float, float, float, float] | None:
        ids = [i for i in (ids if ids is not None else self.visible) if i in self.pos]
        if not ids:
            return None
        pts = [self.project(i) for i in ids]
        us = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        return min(us), min(vs), max(us), max(vs)

    def fit(self, ids=None, margin: float = 70.0):
        ids = [i for i in (ids if ids is not None else self.visible) if i in self.pos]
        if self.mode3d:
            self._set_pivot_from(ids or list(self.pos))
        b = self.bounds(ids)
        if b is None:
            self.scale, self.ox, self.oy = 1.0, self.width / 2, self.height / 2
            return
        x0, y0, x1, y1 = b
        w = max(1.0, x1 - x0)
        h = max(1.0, y1 - y0)
        s = min((self.width - 2 * margin) / w, (self.height - 2 * margin) / h)
        self.scale = min(2.2, max(MIN_SCALE, s))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.ox = self.width / 2 - cx * self.scale
        self.oy = self.height / 2 - cy * self.scale

    def center_on(self, nid: str, scale: float | None = None):
        if nid not in self.pos:
            return
        if scale:
            self.scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        if self.mode3d:
            # Seçilen düğüm döndürme merkezi olur: sonra döndürünce onun etrafında dönülür.
            x, y = self.pos[nid]
            self.pivot = (x, y, self.z.get(nid, 0.0))
            self.invalidate()
        u, v, _, _ = self.project(nid)
        self.ox = self.width / 2 - u * self.scale
        self.oy = self.height / 2 - v * self.scale

    def reset(self):
        """Elle taşınan düğümleri özgün yerine döndürür, açıyı varsayılana alır ve tümünü sığdırır."""
        self.pos = dict(self.base)
        self.z = dict(self.base_z)
        if self.mode3d:
            self.yaw, self.pitch = DEFAULT_YAW, DEFAULT_PITCH
        self.invalidate()
        self.fit()

    def moved_nodes(self) -> list[str]:
        return [i for i, p in self.pos.items()
                if self.base.get(i) != p or self.base_z.get(i, 0.0) != self.z.get(i, 0.0)]

    # ── düğüm taşıma ──────────────────────────────────────────────────────
    def move_node_screen(self, nid: str, sx: float, sy: float):
        """Düğümü ekrandaki (sx, sy) noktasına taşır; 3B'de kameraya olan uzaklığı korunur."""
        if nid not in self.pos:
            return
        u, v = self.to_world(sx, sy)
        if not self.mode3d:
            self.pos[nid] = (u, v)
        else:
            _, _, persp, depth = self.project(nid)
            cx, cy, cz = self.pivot
            x1, y2 = (u - cx) / persp, (v - cy) / persp
            cos_y, sin_y, cos_p, sin_p = self._angles()
            y1 = y2 * cos_p + depth * sin_p
            z1 = -y2 * sin_p + depth * cos_p
            dx = x1 * cos_y - z1 * sin_y
            dz = x1 * sin_y + z1 * cos_y
            self.pos[nid] = (cx + dx, cy + y1)
            self.z[nid] = cz + dz
        if self._proj is not None:
            x, y = self.pos[nid]
            self._proj[nid] = self._project_one(x, y, self.z.get(nid, 0.0), *self._angles())
        self._grid = None

    # ── isabet testi ──────────────────────────────────────────────────────
    def screen_radius(self, nid: str) -> float:
        base = self.radius.get(nid, 6.0) * min(2.5, max(0.45, self.scale))
        if self.mode3d:
            base *= min(1.8, max(0.55, self.project(nid)[2]))
        return max(3.0, base)

    def _build_grid(self):
        grid: dict[tuple[int, int], list[str]] = {}
        c = self._cell
        proj = self._ensure_proj()
        for nid in self.visible:
            p = proj.get(nid)
            if p is None:
                continue
            grid.setdefault((int(math.floor(p[0] / c)), int(math.floor(p[1] / c))), []).append(nid)
        self._grid = grid

    def hit_test(self, sx: float, sy: float, slack_px: float = 10.0) -> str | None:
        if self._grid is None:
            self._build_grid()
        wx, wy = self.to_world(sx, sy)
        max_px = 2.5 * 26 * (1.8 if self.mode3d else 1.0) + slack_px
        reach = max_px / self.scale
        c = self._cell
        gx0, gx1 = int(math.floor((wx - reach) / c)), int(math.floor((wx + reach) / c))
        gy0, gy1 = int(math.floor((wy - reach) / c)), int(math.floor((wy + reach) / c))
        if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) > 4000:  # çok uzaklaşmış görünüm: doğrudan tara
            candidates = [i for i in self.visible if i in self.pos]
        else:
            candidates = []
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    candidates.extend(self._grid.get((gx, gy), ()))
        best, best_key = None, (1e18, 1e18)
        for nid in candidates:
            nx, ny = self.node_screen(nid)
            d = math.hypot(nx - sx, ny - sy)
            if d <= self.screen_radius(nid) + slack_px:
                # Üst üste binenlerde öne (kameraya yakın) olan tutulur.
                key = (round(d / 4.0), self.project(nid)[3]) if self.mode3d else (d, 0.0)
                if key < best_key:
                    best, best_key = nid, key
        return best

    def set_visible(self, ids):
        self.visible = set(i for i in ids if i in self.pos)
        self._grid = None
