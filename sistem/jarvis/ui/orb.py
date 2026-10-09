"""J.A.R.V.I.S. HUD küresi — asıl JARVIS'teki küreyle BİREBİR AYNI.

Işık katmanı ve keskin çizgiler sistem/hud_render.py'den gelir (asıl JARVIS'teki dosyanın değiştirilmemiş kopyası).
Bu modül asıl JARVIS ui.py'deki şu parçaların aynısıdır: _animate'in küreyle ilgili kısmı (ses düzeyi yumuşatma,
ses geçmişi, enerji darbeleri, kamera kayması), _hud_mode, _draw_hud, _draw_orb_particles, _orb_light_frame ve
arka plan parçacıkları. Boyut asıl JARVIS'in FACE formülüyle hesaplanır (face_size), görüntü aynı büyüklükte çıkar.
Değiştirmeden önce: kullanıcı "küre tıpatıp aynı kalsın" dedi (03.10.2026).
"""

from __future__ import annotations

import concurrent.futures
import math
import random
import time
from collections import deque

from PIL import ImageTk

import hud_render
from jarvis.paths import HUD_PLATE
from jarvis.ui import title

# Asıl JARVIS'in yerleşim sabitleri (yalnız küre boyutu formülü için)
_OLD_LEFT_W, _OLD_RIGHT_W, _OLD_HDR_H, _OLD_CONTROL_H, _OLD_FOOTER_H = 310, 340, 72, 126, 30


def face_size(width: int, height: int) -> int:
    """Asıl JARVIS'te kürenin boyutu (FACE) pencere boyutuna göre böyle hesaplanıyordu; aynı ekranda aynı boyut."""
    left = min(_OLD_LEFT_W, int(width * 0.22))
    right = min(_OLD_RIGHT_W, int(width * 0.24))
    center_w = width - left - right
    orb_area_h = height - _OLD_HDR_H - _OLD_CONTROL_H - _OLD_FOOTER_H - 24
    return min(int(orb_area_h * 0.88), int(center_w * 0.74), 560)


def image_size(face: int) -> int:
    return max(96, int(face * 1.24)) // 2 * 2


def _ac(r, g, b, a):
    f = max(0, min(255, int(a))) / 255.0
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"


class OrbView:
    def __init__(self, width: int, height: int, font_display, font_body_bold):
        self.font_display = font_display
        self.font_body_bold = font_body_bold
        self.hud = hud_render.HudOrb(HUD_PLATE, bg=hud_render.BG)
        self.anim = hud_render.HudAnimator("initialising")
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-orb-light")
        self._future = None
        self._last_img = None
        self._photo = None
        self._pasted = None
        self._clock = time.monotonic()
        self._hud_last_t = None
        self._speech_level = 0.0
        self._voice_hist = deque([0.0] * 48, maxlen=48)
        self._speaking_started_at = -1.0e9
        self._last_energy_pulse = -1.0e9
        self._hud_seen_pulse = -1.0e9
        self._was_speaking = False
        self.cam_shift = self.cam_shift_target = 0.0
        self.cam_face = self.cam_face_target = 0.0
        self.box = None
        self._late = []
        self.particles = [
            {'x': random.uniform(0, width), 'y': random.uniform(0, height),
             'vx': random.uniform(-0.15, 0.15), 'vy': random.uniform(-0.15, 0.15),
             'r': random.uniform(0.5, 1.8), 'a': random.randint(15, 70)}
            for _ in range(24)
        ]

    # ── Hal ───────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def mode(s) -> str:
        """Kürenin hâli; öncelik sırası durum yazısıyla aynı."""
        if s.paused:
            return "paused"
        if s.status == "ERROR":
            return "error"
        if s.speaking:
            return "speaking"
        if s.tool:
            return "search" if s.tool == "search_web" else "tool"
        if getattr(s, "browsing", False):
            return "search"         # Chrome araştırması arka planda sürüyor
        if s.muted:
            return "muted"
        return {"LISTENING": "listening", "THINKING": "thinking",
                "INITIALISING": "initialising"}.get(s.status, "listening")

    @staticmethod
    def status_text(s) -> str:
        if s.tool:
            if s.tool == "search_web":
                return "İNTERNETTE ARIYOR"
            if s.tool == "smart_search":
                return "ARIYOR"
            return "ÜZERİNDE ÇALIŞIYOR"
        if getattr(s, "browsing", False):
            return "CHROME'DA GEZİNİYOR"
        if s.status == "LISTENING" and not s.cloud:
            return "BAĞLANTI YOK"
        return "DİNLİYOR"

    def set_camera(self, active: bool, shift: float, face: float, normal_face: float):
        if active:
            self.cam_shift_target, self.cam_face_target = float(shift), float(face)
            if self.cam_face < 1.0:
                self.cam_face = float(normal_face)
        else:
            self.cam_shift_target = 0.0
            self.cam_face_target = float(normal_face)

    def step(self, s, width: int, height: int, webcam_active: bool):
        """Asıl JARVIS _animate'in küreyle ilgili kısmı (her kare)."""
        clock = time.monotonic()
        dt = max(0.0, min(0.12, clock - self._clock))
        self._clock = clock

        def ease(current, target, rise, fall):
            tau = rise if target > current else fall
            return current + (target - current) * (1.0 - math.exp(-dt / tau))

        if s.speaking and not self._was_speaking:
            self._speaking_started_at = clock
            self._last_energy_pulse = clock - 1.0
        self._was_speaking = s.speaking
        output_target = s.output_level if s.speaking else 0.0
        self._speech_level = ease(self._speech_level, output_target, 0.07, 0.15)
        self._voice_hist.append(self._speech_level if s.speaking and not s.paused else 0.0)
        if s.speaking and not s.paused and self._speech_level > 0.20 and clock - self._last_energy_pulse > 0.29:
            self._last_energy_pulse = clock
        _CE = 0.07
        self.cam_shift += (self.cam_shift_target - self.cam_shift) * _CE
        self.cam_face += (self.cam_face_target - self.cam_face) * _CE
        if abs(self.cam_shift_target - self.cam_shift) < 0.5:
            self.cam_shift = self.cam_shift_target
        if abs(self.cam_face_target - self.cam_face) < 0.5:
            self.cam_face = self.cam_face_target
            if not webcam_active:
                self.cam_face = self.cam_face_target = 0.0
        for p in self.particles:
            p['x'] = (p['x'] + p['vx']) % width
            p['y'] = (p['y'] + p['vy']) % height

    # ── Çizim ─────────────────────────────────────────────────────────────────────────────
    def orb_rgb(self, s):
        return (30, 60, 55) if s.paused else (45, 185, 125)

    def draw_particles(self, c, s):
        """Kürenin karesine düşmeyen arka plan parçacıkları (kareye düşenler küreden sonra çizilir)."""
        R, G, B = self.orb_rgb(s)
        box = self.box
        self._late = []
        for p in self.particles:
            if box and box[0] <= p['x'] <= box[2] and box[1] <= p['y'] <= box[3]:
                self._late.append(p)
                continue
            col = _ac(255, 110, 0, p['a']) if s.speaking else _ac(R, G, B, p['a'])
            r = p['r']
            c.create_oval(p['x'] - r, p['y'] - r, p['x'] + r, p['y'] + r, fill=col, outline="")

    def draw(self, c, s, fcx: int, fcy: int, face: int, cam: bool = False):
        now = time.monotonic()
        last = self._hud_last_t
        dt = 0.0 if last is None else max(0.0, min(0.1, now - last))
        self._hud_last_t = now
        mode = self.mode(s)
        anim = self.anim
        pulse = self._last_energy_pulse != self._hud_seen_pulse
        self._hud_seen_pulse = self._last_energy_pulse
        anim.step(dt, mode, mic=s.mic_level if mode == "listening" else 0.0,
                  voice=self._speech_level, voice_hist=self._voice_hist, pulse=pulse)
        success_age = now - s.success_at
        flash = 0.30 * (1.0 - success_age / 0.8) if 0 <= success_age < 0.8 else 0.0
        base_face = int(self.cam_face) if self.cam_face > 1 else face
        size = image_size(base_face)
        FCX = fcx
        FCY = fcy + int(self.cam_shift)
        img = self._light_frame(anim.raster_job(size, flash=flash, sweep_allowed=not cam))
        photo = self._photo
        if photo is None or photo.width() != img.width:
            photo = self._photo = ImageTk.PhotoImage(img)
            self._pasted = img
        elif img is not self._pasted:
            photo.paste(img)
            self._pasted = img
        c.create_image(FCX, FCY, image=photo)
        half = img.width / 2
        self.box = (FCX - half, FCY - half, FCX + half, FCY + half, hud_render.R_FRAC * img.width)
        label = self.status_text(s) if mode in ("tool", "search", "listening") else hud_render.mode_label(mode)
        R = hud_render.R_FRAC * size
        for it in anim.vectors(FCX, FCY, R, label=label, show_text=True):
            kind = it[0]
            if kind == "line":
                c.create_line(it[1], it[2], it[3], it[4], fill=it[5], width=it[6])
            elif kind == "arc":
                c.create_arc(it[1], it[2], it[3], it[4], start=it[5], extent=it[6], outline=it[7], width=it[8],
                             style="arc")
            elif kind == "oval":
                c.create_oval(it[1], it[2], it[3], it[4], outline=it[5], width=it[6])
            elif kind == "dot":
                c.create_oval(it[1], it[2], it[3], it[4], fill=it[5], outline="")
            elif kind == "text":
                # J.A.R.V.I.S. yazısı kullanıcının fotoğrafındaki gibi (ui/title.py, 03.10): ışıması ışık katmanında,
                # harfleri halkaların üstünde ayrı saydam resim
                if it[6] == "name" and title.available():
                    self._draw_title(c, it[4], FCX, FCY, img.width, size)
                    continue
                font = self.font_display(-it[5]) if it[6] == "name" else self.font_body_bold(-it[5])
                c.create_text(it[1], it[2], text=it[3], fill=it[4], font=font)
        error_age = now - s.error_at
        if not cam and 0 <= error_age < 0.48:
            alpha = int(175 * (1.0 - error_age / 0.48))
            rr = int(R * 0.80)
            for start in (22, 205):
                c.create_arc(FCX - rr, FCY - rr, FCX + rr, FCY + rr, start=start, extent=96,
                             outline=_ac(245, 137, 42, alpha), width=2, style="arc")
        self._draw_late_particles(c, s)

    def _draw_title(self, c, color_hex: str, fcx: float, fcy: float, img_w: int, size: int):
        rgba, (bx, by) = title.overlay(size, title.text_ratio(color_hex))
        cache = self.__dict__.setdefault("_title_photos", {})
        photo = cache.get(id(rgba))
        if photo is None:
            if len(cache) > 24:
                cache.clear()
            photo = cache[id(rgba)] = (ImageTk.PhotoImage(rgba), rgba)
        c.create_image(fcx - img_w / 2 + bx, fcy - img_w / 2 + by, image=photo[0], anchor="nw")

    def _draw_late_particles(self, c, s):
        box = self.box
        if not self._late or not box:
            return
        cx, cy, R = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[4]
        R0, G0, B0 = self.orb_rgb(s)
        for p in self._late:
            d = math.hypot(p['x'] - cx, p['y'] - cy) / max(1.0, R)
            fade = max(0.0, min(1.0, (d - 1.30) / 0.30))
            if fade <= 0.02:
                continue
            a = p['a'] * fade
            col = _ac(255, 110, 0, a) if s.speaking else _ac(R0, G0, B0, a)
            r = p['r']
            c.create_oval(p['x'] - r, p['y'] - r, p['x'] + r, p['y'] + r, fill=col, outline="")

    def _light_frame(self, job: dict):
        """Işık katmanı ayrı iş parçacığında üretilir; ana iş parçacığı beklemez (≈1 kare gecikme)."""
        fut = self._future
        if fut is not None and fut.done():
            self._last_img = fut.result()
            fut = self._future = None
        if fut is None:
            self._future = self._executor.submit(
                lambda: title.add_title(self.hud.render(**job), job["size"], job.get("bright", 1.0)))
        if self._last_img is None:
            self._last_img = self._future.result()
            self._future = None
        return self._last_img
