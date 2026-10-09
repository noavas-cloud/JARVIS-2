"""JARVIS HUD küresi, 2. sürüm: her durumun kendine özgü bir görünümü ve hareketi var.

İki katman:
  • Işık katmanı (HudOrb.render, ayrı iş parçacığında): düz zemin + yalnız kürenin içinde kalan teknik çizim dokusu,
    yumuşak ışımalar, bölmeli bant dolgusu, radar taraması. Yumuşak şeyler oldukları için yarım çözünürlükte
    numpy ile hesaplanıp büyütülür (her kare ~5-8 ms). Kürenin dışında zemin tek renktir; görüntünün kenarı zeminle
    birebir aynıdır, kare görünmez.
  • Keskin katman (HudAnimator.vectors): halkalar, kadran çentikleri, bant bölmeleri, sarı yay, yazı. Tk bunları
    vektör olarak çizer → Retina ekranda da jilet gibi keskin kalır (Tk resimleri Retina'da 2× büyütüp yumuşatır).

Durumlar (HudAnimator.step(mode=...)) ve ayırt edici halleri:
  listening  camgöbeği · bant ve kadran mikrofonla parlar
  speaking   altın (referans: holo-gestures JARVIS modu) · 1,05 sn'lik nefes ışıması, kadran sesle tepeden yanar
  (kürenin dışında dalga efekti yok: kullanıcı istemedi, 02.10.2026)
  thinking   buz beyazı · halkalar hızlanır, sarı yay tarar
  search     mor · ortada dönen dünya ızgarası, kadranda radar taraması, yörüngede veri noktaları
  tool       buz beyazı · radar taraması
  muted      kırmızı · her şey durur, yavaş kırmızı nefes, üstte üstü çizili mikrofon simgesi
  paused     gri bekleme · halkalar durur, küre biraz küçülür, çok yavaş nefes, etrafında tek bir kuyruklu yıldız döner,
             üstte duraklat simgesi
  initialising, error

Referans (yalnız JARVIS animasyonu): dışta bir çeyreği boş, ışıyan halka 14 sn'de bir döner; içte kesik çizgili halka
ters yöne döner (konuşurken 5 sn); konuşurken ışıma 1,05 sn'de bir nefes alır; ortada ad, altında aralıklı harflerle
durum yazısı. Yalnız numpy + Pillow; Tk'ye bağımlı değildir.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

R_FRAC = 0.375          # dış halka yarıçapı / görüntü kenarı (dış ışımaya ~0,33 R pay kalır)

BAND = (92, 178, 211)
RING = (115, 193, 204)
YELLOW = (214, 192, 82)
TEXT = (215, 239, 244)
BG = (10, 29, 35)

_S = dict(gap=25.7, inner=-40.0, dial=3.0, bands=-2.0, yellow=8.0, yellow_gain=0.85, bright=1.0, halo=0.55,
          sweep=0.0, globe=0.0, comet=0.0, mute=0.0, pause=0.0, breathe=3.2, breathe_amp=0.30, scale=1.0,
          accent=(95, 208, 240), band=BAND, ring=RING)
STYLES = {
    "listening": dict(_S),
    "speaking": dict(_S, accent=(255, 204, 110), band=(104, 170, 196), ring=(150, 196, 196), bright=1.05, halo=0.62,
                     inner=72.0, dial=2.0, bands=-3.0, yellow=10.0, breathe=1.05, breathe_amp=0.55),
    "thinking": dict(_S, accent=(214, 240, 250), band=(110, 190, 222), ring=(140, 206, 216), bright=1.06, halo=0.58,
                     gap=80.0, inner=-90.0, dial=-14.0, bands=22.0, yellow=150.0, yellow_gain=1.25, breathe=0.8,
                     breathe_amp=0.30),
    "initialising": dict(_S, accent=(170, 215, 232), band=(90, 150, 175), ring=(110, 170, 182), bright=0.9, halo=0.45,
                         gap=120.0, inner=-100.0, dial=-10.0, bands=15.0, yellow=120.0, breathe=0.9),
    "search": dict(_S, accent=(172, 142, 255), band=(118, 136, 232), ring=(132, 168, 214), bright=1.05, halo=0.62,
                   gap=60.0, inner=-70.0, dial=-8.0, bands=10.0, yellow=60.0, sweep=1.0, globe=1.0, breathe=1.4),
    "tool": dict(_S, accent=(214, 240, 250), band=(110, 190, 222), ring=(140, 206, 216), bright=1.05, halo=0.55,
                 gap=50.0, inner=-60.0, dial=-8.0, bands=10.0, yellow=60.0, sweep=1.0, breathe=1.4),
    "muted": dict(_S, accent=(255, 77, 94), band=(72, 94, 100), ring=(96, 122, 128), bright=0.82, halo=0.42,
                  gap=0.0, inner=0.0, dial=0.0, bands=0.0, yellow=0.0, yellow_gain=0.30, mute=1.0, breathe=2.6,
                  breathe_amp=0.55),
    "paused": dict(_S, accent=(126, 152, 160), band=(56, 80, 87), ring=(84, 108, 115), bright=0.55, halo=0.22,
                   gap=0.0, inner=0.0, dial=0.0, bands=0.0, yellow=0.0, yellow_gain=0.25, comet=1.0, pause=1.0,
                   breathe=5.0, breathe_amp=0.60, scale=0.95),
    "error": dict(_S, accent=(255, 96, 72), band=(110, 110, 120), ring=(150, 140, 140), halo=0.6, gap=40.0, inner=-40.0,
                  dial=0.0, bands=0.0, yellow=0.0, breathe=0.5, breathe_amp=0.5),
}
LABELS = {"listening": "DİNLİYOR", "speaking": "KONUŞUYOR", "thinking": "DÜŞÜNÜYOR", "initialising": "BAŞLATILIYOR",
          "search": "İNTERNETTE ARIYOR", "tool": "ÇALIŞIYOR", "muted": "MİKROFON KAPALI", "paused": "DURAKLATILDI",
          "error": "HATA"}
_COLOR_KEYS = ("accent", "band", "ring")


def _mix(rgb, a: float, bg=BG) -> str:
    """rgb rengini a (0..1, üstü parlatır) oranında zemine karıştırıp Tk renk dizisi döndürür."""
    a = max(0.0, a)
    out = []
    for v, b in zip(rgb, bg):
        x = b + (v - b) * a if a <= 1.0 else v + (255 - v) * min(1.0, (a - 1.0) * 0.8)
        out.append(max(0, min(255, int(x))))
    return "#%02x%02x%02x" % tuple(out)


def _arc_w(ang, start: float, extent: float, soft: float):
    """Açısal yay ağırlığı (0..1): start→start+extent (derece, saat yönünün tersi), uçları `soft` derece yumuşak."""
    d = (ang - start) % 360.0
    s = np.where(d < extent, np.minimum(d, extent - d), -np.minimum(d - extent, 360.0 - d))
    return np.clip(0.5 + s / soft, 0.0, 1.0)


def _smooth_band(rr, r0: float, r1: float, soft: float):
    return np.clip((rr - r0) / soft + 0.5, 0, 1) * np.clip((r1 - rr) / soft + 0.5, 0, 1)


class HudOrb:
    """render(...) her çağrıda size x size RGB görüntü döndürür (ışık katmanı; keskin çizgiler HudAnimator.vectors)."""

    def __init__(self, plate_path: str | Path | None = None, bg=BG):
        self._plate_src = Path(plate_path) if plate_path else None
        self._bg = tuple(bg)
        self._size = 0
        self._plate_cache: dict = {}

    # ── Hazırlık ────────────────────────────────────────────────────────────────────────────────
    def _ensure(self, size: int):
        if size == self._size:
            return
        self._size = size
        self._plate_cache = {}
        self.R = R = size * R_FRAC
        self._plate = self._make_plate(size, R)
        n = self._n = max(16, size // 2)
        Rh = R * n / size
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        c = (n - 1) / 2.0
        rr = (np.hypot(xx - c, yy - c) / Rh).ravel()
        ang = ((np.degrees(np.arctan2(-(yy - c), xx - c)) + 360.0) % 360.0).ravel()
        g = lambda r0, w: np.exp(-((rr - r0) / w) ** 2)          # noqa: E731

        # Duran ışımalar: dış ışık halesi (durum renginde) ve halkaların yumuşak ışığı (halka renginde)
        halo = np.where(rr > 1.0, np.exp(-((rr - 1.0) / 0.11) ** 2), np.exp(-((rr - 1.0) / 0.07) ** 2))
        halo = halo + 0.10 * np.exp(-(rr / 0.50) ** 2)              # iç diskte çok hafif aydınlık
        halo = halo * np.clip((1.31 - rr) / 0.05, 0, 1)            # görüntü kenarında tam sıfır
        self._halo = halo.astype(np.float32)
        glow = (0.55 * g(0.511, 0.016) + 0.20 * g(0.465, 0.012) + 0.30 * g(0.772, 0.014) + 0.16 * g(1.04, 0.012)
                + 0.16 * _smooth_band(rr, 0.905, 0.97, 0.03) + 0.12 * g(0.572, 0.012))
        self._glow = glow.astype(np.float32)

        def sub(r0, r1):
            idx = np.nonzero((rr >= r0) & (rr <= r1))[0]
            return idx, rr[idx], ang[idx]
        self._gap = sub(0.93, 1.07)
        self._dial = sub(0.86, 1.02)
        self._band = sub(0.60, 0.80)
        self._yel = sub(0.55, 0.66)
        self._sweep = sub(0.44, 1.00)

    def _make_plate(self, size: int, R: float) -> Image.Image:
        """Düz zemin; görselin teknik çizim dokusu yalnız dış halkanın içinde, kenara doğru zemine kaybolur."""
        bg = np.array(self._bg, np.float32)
        out = np.empty((size, size, 3), np.float32)
        out[:] = bg
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
        cc = (size - 1) / 2.0
        rr = np.hypot(xx - cc, yy - cc) / R
        if self._plate_src and self._plate_src.exists():
            try:
                src = Image.open(self._plate_src).convert("RGB")
                scale = R / 192.0                           # görselde merkez (258, 248), dış halka 192 px
                src = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))), Image.LANCZOS)
                tile = Image.new("RGB", (size, size), self._bg)
                tile.paste(src, (-int(258 * scale - cc), -int(248 * scale - cc)))
                a = np.asarray(tile, np.float32)
                lum = a.mean(axis=2, keepdims=True)
                tex = a * np.minimum(1.0, 52.0 / np.maximum(lum, 1.0))   # yalnız soluk çizimler kalsın
                inside = np.clip((0.93 - rr) / 0.10, 0, 1)
                # Görseldeki büyük "J.A.R.V.I.S." yazısı dokuda hayalet gibi kalmasın: o şerit silinir.
                dx, dy = np.abs(xx - cc) / R, np.abs(yy - cc) / R
                stripe = np.clip((dy - 0.10) / 0.05, 0, 1) + np.clip((dx - 0.64) / 0.05, 0, 1)
                inside = inside * np.clip(stripe, 0, 1)
                out = bg + (tex - bg) * (inside * 0.85)[..., None]
            except OSError:
                pass
        inner = np.clip((0.50 - rr) / 0.05, 0, 1)[..., None]            # yazının arkası biraz daha koyu
        out = out * (1 - 0.22 * inner)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")

    def _plate_at(self, bright: float) -> Image.Image:
        key = round(min(1.0, bright), 2)
        img = self._plate_cache.get(key)
        if img is None:
            bg = self._bg
            # Zemin rengi değişmesin: yalnız zeminden farkı ölçekle.
            lut = []
            for b in bg:
                lut += [max(0, min(255, int(b + (v - b) * key))) for v in range(256)]
            img = self._plate if key >= 0.995 else self._plate.point(lut)
            if len(self._plate_cache) > 40:
                self._plate_cache.clear()
            self._plate_cache[key] = img
        return img

    # ── Kare ────────────────────────────────────────────────────────────────────────────────────
    def render(self, *, size: int, accent=(95, 208, 240), band_rgb=BAND, ring_rgb=RING, bright: float = 1.0,
               halo: float = 0.55, ring_glow: float = 1.0, gap_deg: float = 0.0, band_deg: float = 0.0,
               band_gain: float = 1.0, yellow_deg: float = 0.0, yellow_gain: float = 1.0, ticks=None,
               sweep_deg: float | None = None, sweep_gain: float = 0.0, scale: float = 1.0) -> Image.Image:
        """size: kenar (px, çift). *_deg: dönüş açıları (derece, saat yönünün tersi pozitif). ticks: 36 değerlik
        (10°'lik ekran dilimleri) 0..1 kadran parlaklığı. scale: kürenin
        boyutu (duraklatınca biraz küçülür)."""
        self._ensure(size)
        n = self._n
        out = np.zeros((n * n, 3), np.float32)
        acc = np.asarray(accent, np.float32)
        b = max(0.0, bright)
        out += (self._halo * (halo * b))[:, None] * acc
        out += (self._glow * (ring_glow * b))[:, None] * np.asarray(ring_rgb, np.float32)
        k = 1.0 / max(0.5, scale)                                  # küçülünce yarıçaplar içeri kayar

        def layer(spec, values, color, gain):
            idx = spec[0]
            out[idx] += (values * gain)[:, None] * np.asarray(color, np.float32)

        # Boşluklu dış halkanın ışığı (dönen 270°'lik yay)
        idx, rr, ang = self._gap
        rr = rr * k
        layer(self._gap, np.exp(-((rr - 1.0) / 0.024) ** 2) * _arc_w(ang, gap_deg, 270.0, 10.0), acc, 0.95 * b)

        # Bölmeli bant dolgusu (keskin bölme çizgileri ve kenarlar vektörde)
        idx, rr, ang = self._band
        rr = rr * k
        rad = _smooth_band(rr, 0.625, 0.770, 0.02)
        w = (0.72 * _arc_w(ang, band_deg - 10.0, 182.0, 6.0) + 0.42 * _arc_w(ang, band_deg - 58.0, 48.0, 6.0)
             + 0.30 * _arc_w(ang, band_deg + 172.0, 14.0, 6.0) + 0.10)
        layer(self._band, rad * w, band_rgb, 0.70 * b * band_gain)

        # Sarı yayın ışığı
        idx, rr, ang = self._yel
        rr = rr * k
        yel = np.exp(-((rr - 0.607) / 0.022) ** 2) * _arc_w(ang, yellow_deg + 146.0, 68.0, 4.0)
        yel = yel + 0.8 * np.exp(-((rr - 0.594) / 0.016) ** 2) * _arc_w(ang, yellow_deg + 64.0, 52.0, 4.0)
        layer(self._yel, yel, YELLOW, 0.55 * b * yellow_gain)

        # Kadran: sesle tepeden yanan çentiklerin ışığı (ekrana göre sabit açı)
        if ticks is not None:
            idx, rr, ang = self._dial
            rr = rr * k
            table = np.repeat(np.asarray(ticks, np.float32), 10)[:360]
            lit = _smooth_band(rr, 0.905, 0.975, 0.03) * table[ang.astype(np.int16) % 360]
            layer(self._dial, lit, acc, 1.1 * b)

        # Radar taraması: önde parlak kenar, arkasında sönen iz
        if sweep_gain > 0.01 and sweep_deg is not None:
            idx, rr, ang = self._sweep
            rr = rr * k
            diff = (sweep_deg - ang) % 360.0
            v = _smooth_band(rr, 0.47, 0.975, 0.04) * np.exp(-diff / 34.0) * (diff < 300)
            layer(self._sweep, v, acc, 0.55 * sweep_gain * b)

        half = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8).reshape(n, n, 3), "RGB")
        light = half.resize((size, size), Image.BILINEAR)
        return ImageChops.add(self._plate_at(b), light)


def _lerp(a, b, t):
    return a + (b - a) * t


class HudAnimator:
    """Durum geçişlerini yumuşatır (renkler, hızlar, ışıma), açıları ilerletir; ışık katmanı için
    render() argümanlarını ve keskin katman için çizim listesini üretir."""

    def __init__(self, mode: str = "initialising"):
        self.mode = mode
        self.style = {k: (tuple(float(x) for x in v) if k in _COLOR_KEYS else float(v))
                      for k, v in STYLES[mode].items()}
        self.ang = dict(gap=90.0, inner=0.0, dial=0.0, bands=0.0, yellow=0.0, globe=0.0, comet=90.0, orbit=0.0,
                        sweep=90.0)
        self.t = 0.0
        self.breath_phase = 0.0
        self.mic = 0.0
        self.voice = 0.0
        self.ticks = None
        self.last_dt = 1 / 30

    # ── Zaman adımı ───────────────────────────────────────────────────────────────────────────────
    def step(self, dt: float, mode: str, *, mic: float = 0.0, voice: float = 0.0, voice_hist=None,
             pulse: bool = False):
        dt = max(0.0, min(0.1, dt))
        self.last_dt = dt if dt > 0 else self.last_dt
        if mode not in STYLES:
            mode = "listening"
        self.mode = mode
        self.t += dt
        target = STYLES[mode]
        st = self.style
        ease_c = 1.0 - math.exp(-dt / 0.28)       # renkler/ışıma ≈0,3 sn'de geçer
        ease_s = 1.0 - math.exp(-dt / 0.45)       # hızlar biraz daha yavaş: dönüş hızlanıp yavaşlar, sıçramaz
        for key, val in target.items():
            if key in _COLOR_KEYS:
                st[key] = tuple(_lerp(a, float(b), ease_c) for a, b in zip(st[key], val))
            elif key in ("gap", "inner", "dial", "bands", "yellow"):
                st[key] = _lerp(st[key], float(val), ease_s)
            else:
                st[key] = _lerp(st[key], float(val), ease_c)
        for key in ("gap", "inner", "dial", "bands", "yellow"):
            self.ang[key] = (self.ang[key] + st[key] * dt) % 360.0
        self.ang["globe"] = (self.ang["globe"] + 40.0 * dt) % 360.0
        self.ang["orbit"] = (self.ang["orbit"] - 150.0 * dt) % 360.0
        self.ang["comet"] = (self.ang["comet"] - 60.0 * dt) % 360.0     # bekleme: 6 sn'de bir tur
        self.ang["sweep"] = (self.ang["sweep"] - 150.0 * dt) % 360.0    # radar: saat yönünde ~2,4 sn'de bir tur
        self.breath_phase = (self.breath_phase + dt / max(0.2, st["breathe"])) % 1.0
        self.mic += (max(0.0, min(1.0, mic)) - self.mic) * (1.0 - math.exp(-dt / 0.10))
        self.voice = max(0.0, min(1.0, voice))

        # Kadran: konuşurken en yeni ses tepede, eskisi iki yana akar
        self.ticks = None
        if mode == "speaking" and voice_hist:
            raw = list(voice_hist)
            m = len(raw)
            self.ticks = []
            for k in range(36):
                off = abs(((k * 10 + 5) - 90 + 180) % 360 - 180)
                self.ticks.append(min(1.0, raw[m - 1 - int(off / 180.0 * (m - 1))] * 1.6))
        elif mode == "listening" and self.mic > 0.05:
            self.ticks = [0.35 * self.mic] * 36

    def breath(self) -> float:
        b = 0.5 - 0.5 * math.cos(math.tau * self.breath_phase)
        return 1.0 - self.style["breathe_amp"] + self.style["breathe_amp"] * b

    # ── Işık katmanı ──────────────────────────────────────────────────────────────────────────────
    def raster_job(self, size: int, *, flash: float = 0.0, sweep_allowed: bool = True) -> dict:
        """HudOrb.render argümanları. Görüntü bir kare sonra ekrana geleceği için açılar bir kare ileriye alınır
        (keskin katmanla hizalı kalsın)."""
        st, a, ahead = self.style, self.ang, self.last_dt
        listen_boost = 0.25 * self.mic if self.mode == "listening" else 0.0
        voice_boost = 0.35 * self.voice if self.mode == "speaking" else 0.0
        return dict(
            size=size, accent=st["accent"], band_rgb=st["band"], ring_rgb=st["ring"],
            bright=min(1.3, st["bright"] + flash),
            halo=st["halo"] * self.breath() + listen_boost + voice_boost,
            gap_deg=a["gap"] + st["gap"] * ahead, band_deg=a["bands"] + st["bands"] * ahead,
            band_gain=1.0 + 0.6 * listen_boost + 0.5 * voice_boost,
            yellow_deg=a["yellow"] + st["yellow"] * ahead, yellow_gain=st["yellow_gain"],
            ticks=self.ticks,
            sweep_deg=(a["sweep"] - 150.0 * ahead) % 360.0, sweep_gain=st["sweep"] if sweep_allowed else 0.0,
            scale=st["scale"])

    # ── Keskin katman ─────────────────────────────────────────────────────────────────────────────
    def vectors(self, cx: float, cy: float, R: float, *, label: str | None = None, show_text: bool = True,
                bg=BG) -> list:
        """Tk'de çizilecek öğeler (sırasıyla):
        ("oval", x0, y0, x1, y1, renk, kalınlık) · ("arc", x0, y0, x1, y1, başlangıç, açıklık, renk, kalınlık)
        ("line", x0, y0, x1, y1, renk, kalınlık) · ("dot", x0, y0, x1, y1, dolgu) · ("text", x, y, yazı, renk, px, tür)"""
        st, a = self.style, self.ang
        R = R * st["scale"]
        b = st["bright"]
        acc, ring, band = st["accent"], st["ring"], st["band"]
        items: list = []

        def circle(r, color, w):
            items.append(("oval", cx - r * R, cy - r * R, cx + r * R, cy + r * R, color, w))

        def arc(r, start, extent, color, w):
            if extent <= 0.5:
                return
            items.append(("arc", cx - r * R, cy - r * R, cx + r * R, cy + r * R, start % 360.0, extent, color, w))

        def radial(deg, r0, r1, color, w):
            ca, sa = math.cos(math.radians(deg)), -math.sin(math.radians(deg))
            items.append(("line", cx + ca * r0 * R, cy + sa * r0 * R, cx + ca * r1 * R, cy + sa * r1 * R, color, w))

        def dot(deg, r, rad_px, color):
            x = cx + math.cos(math.radians(deg)) * r * R
            y = cy - math.sin(math.radians(deg)) * r * R
            items.append(("dot", x - rad_px, y - rad_px, x + rad_px, y + rad_px, color))

        px = max(0.6, R / 260.0)            # çizgi kalınlığı ölçeği (küre büyüklüğüne göre)

        # Dış: soluk tam halka + kadranla dönen köşebentler
        circle(1.04, _mix(ring, 0.30 * b, bg), 1.0 * px)
        for s0, ext in ((22, 8), (40, 7), (314, 8), (328, 5)):
            arc(1.04, a["dial"] + s0, ext, _mix(ring, 0.80 * b, bg), 3.0 * px)
        # Referanstaki boşluklu halka: 270° ışıyan yay (rengi durumu gösterir)
        arc(1.0, a["gap"], 270.0, _mix(acc, 0.95 * b * (0.85 + 0.15 * self.breath()), bg), 2.4 * px)
        head = a["gap"] + 270.0
        radial(head, 0.975, 1.025, _mix(acc, 1.1 * b, bg), 2.0 * px)
        radial(a["gap"], 0.985, 1.015, _mix(acc, 0.6 * b, bg), 1.6 * px)

        # Kadran: 120 çentik (3°), her 5.'si uzun; ses/mikrofon düzeyi ekrana göre sabit dilimlerle parlatır
        ticks = self.ticks
        for k in range(120):
            deg = a["dial"] + k * 3.0
            long_ = k % 5 == 0
            lit = 0.0
            if ticks is not None:
                lit = ticks[int(deg % 360.0) // 10 % 36]
            base = (0.62 if long_ else 0.34) * b
            if lit > 0.02:
                col = tuple(_lerp(ring[i], acc[i], min(1.0, lit * 1.4)) for i in range(3))
                color = _mix(col, base + 0.85 * lit * b, bg)
            else:
                color = _mix(ring, base, bg)
            radial(deg, 0.905 if long_ else 0.93, 0.97, color, (1.4 if long_ else 0.9) * px)
        for s0, ext in ((350, 12), (16, 24), (112, 16), (318, 22)):
            arc(0.962, a["dial"] + s0, ext, _mix(ring, 0.85 * b, bg), 2.4 * px)
        circle(0.895, _mix(ring, 0.18 * b, bg), 0.8 * px)

        # Bant: dış kenar ışığı, iç ince çizgiler, keskin bölme çizgileri (dolgu ışık katmanında)
        bd = a["bands"]
        arc(0.772, bd - 58.0, 244.0, _mix((150, 210, 228) if band == BAND else band, 0.85 * b, bg), 1.6 * px)
        arc(0.625, bd - 10.0, 182.0, _mix(band, 0.45 * b, bg), 0.9 * px)
        arc(0.663, bd - 10.0, 180.0, _mix(band, 0.30 * b, bg), 0.8 * px)
        arc(0.720, bd - 10.0, 180.0, _mix(band, 0.24 * b, bg), 0.8 * px)
        divider = _mix(band, 0.30 * b, bg)
        for deg in range(-57, 187, 12):
            radial(bd + deg, 0.628, 0.768, divider, 1.1 * px)
        arc(0.688, bd + 170.0, 180.0, _mix(ring, 0.20 * b, bg), 0.8 * px)

        # Sarı yay + noktalar
        yg = st["yellow_gain"] * b
        yd = a["yellow"]
        ycol = _mix(YELLOW, min(1.15, 1.0 * yg), bg)
        arc(0.608, yd + 146.0, 68.0, ycol, 2.0 * px)
        radial(yd + 146.0, 0.600, 0.640, ycol, 1.6 * px)
        radial(yd + 214.0, 0.600, 0.640, ycol, 1.6 * px)
        arc(0.633, yd + 206.0, 8.0, ycol, 1.6 * px)
        for d0 in (66, 78, 90, 102, 114):
            dot(yd + d0, 0.594, 2.0 * px, ycol)

        # Referanstaki iç halka: kesik çizgili, ters yöne döner
        dash_col = _mix(acc, 0.62 * b, bg)
        for k in range(30):
            arc(0.572, a["inner"] + k * 12.0, 7.0, dash_col, 1.3 * px)

        # İç halkalar
        circle(0.511, _mix(ring, 0.95 * b, bg), 2.2 * px)
        circle(0.465, _mix(ring, 0.32 * b, bg), 0.9 * px)

        # Arama: ortada dönen dünya ızgarası + yörüngede veri noktaları
        gw = st["globe"]
        if gw > 0.03:
            gcol = _mix(acc, 0.55 * gw * b, bg)
            rg = 0.40 * R
            for k in range(6):
                phi = math.radians(a["globe"] + k * 30.0)
                hw = abs(math.sin(phi)) * rg
                if hw > 1.0:
                    items.append(("oval", cx - hw, cy - rg, cx + hw, cy + rg, gcol, 0.9 * px))
            items.append(("line", cx - rg, cy, cx + rg, cy, gcol, 0.9 * px))
            for f in (0.5, 0.86):
                y = rg * f
                half = math.sqrt(max(0.0, rg * rg - y * y))
                items.append(("line", cx - half, cy - y, cx + half, cy - y, gcol, 0.9 * px))
                items.append(("line", cx - half, cy + y, cx + half, cy + y, gcol, 0.9 * px))
            for k in range(3):
                d0 = a["orbit"] + k * 120.0
                for j in range(5):
                    dot(d0 + j * 4.0, 0.84, (2.6 - 0.4 * j) * px, _mix(acc, (1.1 - 0.2 * j) * gw * b, bg))
        # Radar taramasının önündeki keskin çizgi
        if st["sweep"] > 0.03:
            radial(a["sweep"], 0.48, 0.97, _mix(acc, 1.05 * st["sweep"] * b, bg), 1.6 * px)

        # Bekleme: tek kuyruklu yıldız yavaşça döner
        cw = st["comet"]
        if cw > 0.03:
            for j in range(16):
                dot(a["comet"] + j * 3.2, 0.84, max(0.6, (3.2 - 0.17 * j) * px),
                    _mix((200, 225, 232), (1.0 - j / 16.0) * cw * 1.1, bg))

        # Yazılar ve simge
        if show_text:
            items.append(("text", cx, cy - 0.02 * R, "J.A.R.V.I.S.", _mix(TEXT, 0.98 * min(1.0, b + 0.15), bg),
                          max(10, int(0.150 * R)), "name"))
            if label:
                items.append(("text", cx, cy + 0.17 * R, " ".join(label),
                              _mix(acc, 1.0, bg), max(8, int(0.052 * R)), "label"))
            iy = cy - 0.27 * R
            mw = st["mute"]
            if mw > 0.05:                                   # üstü çizili mikrofon
                col = _mix(acc, mw, bg)
                s = 0.075 * R
                items.append(("oval", cx - 0.36 * s, iy - s, cx + 0.36 * s, iy + 0.25 * s, col, 1.6 * px))
                items.append(("arc", cx - 0.62 * s, iy - 0.55 * s, cx + 0.62 * s, iy + 0.62 * s, 200.0, 140.0, col,
                              1.4 * px))
                items.append(("line", cx, iy + 0.62 * s, cx, iy + 0.95 * s, col, 1.4 * px))
                items.append(("line", cx - 0.8 * s, iy - 1.0 * s, cx + 0.8 * s, iy + 0.95 * s, col, 1.8 * px))
            pw = st["pause"]
            if pw > 0.05:                                   # duraklat simgesi
                col = _mix((200, 225, 232), pw * 0.9, bg)
                s = 0.065 * R
                items.append(("dot", cx - 0.75 * s, iy - s, cx - 0.25 * s, iy + s, col))
                items.append(("dot", cx + 0.25 * s, iy - s, cx + 0.75 * s, iy + s, col))
        return items


def mode_label(mode: str) -> str:
    return LABELS.get(mode, "")


__all__ = ["HudOrb", "HudAnimator", "STYLES", "LABELS", "BG", "R_FRAC", "mode_label"]

if __name__ == "__main__":       # hızlı ölçüm
    hud, an = HudOrb(Path(__file__).parent / "Icon" / "jarvis_hud.webp"), HudAnimator("speaking")
    for m in ("listening", "speaking", "search", "paused"):
        an.step(0.03, m, mic=0.5, voice=0.6, voice_hist=[0.5] * 60, pulse=True)
        hud.render(**an.raster_job(660))
        t0 = time.perf_counter()
        for _ in range(20):
            an.step(0.025, m, mic=0.5, voice=0.6, voice_hist=[0.5] * 60, pulse=True)
            hud.render(**an.raster_job(660))
        r_ms = (time.perf_counter() - t0) / 20 * 1000
        t0 = time.perf_counter()
        for _ in range(20):
            an.vectors(330, 330, 247, label=mode_label(m))
        print(f"{m}: ışık {r_ms:.1f} ms, vektör listesi {(time.perf_counter() - t0) / 20 * 1000:.2f} ms, "
              f"{len(an.vectors(330, 330, 247, label=mode_label(m)))} öğe")
