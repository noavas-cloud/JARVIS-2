"""Sentetik el iskeleti üretici (MediaPipe 21 nokta düzeni) — testler için."""

from __future__ import annotations

import math

# El-yerel izotropik koordinatlar: bilek (0,0), parmaklar yukarı (-y). Orta parmak kökü uzunluğu 1.05.
_BASE = {
    0: (0.0, 0.0),
    1: (-0.35, -0.25), 2: (-0.55, -0.45), 3: (-0.70, -0.65), 4: (-0.85, -0.85),
    5: (-0.25, -1.00), 6: (-0.28, -1.35), 7: (-0.30, -1.60), 8: (-0.32, -1.85),
    9: (0.00, -1.05), 10: (0.00, -1.45), 11: (0.00, -1.70), 12: (0.00, -1.95),
    13: (0.22, -1.00), 14: (0.24, -1.35), 15: (0.25, -1.58), 16: (0.26, -1.80),
    17: (0.42, -0.90), 18: (0.46, -1.15), 19: (0.49, -1.35), 20: (0.50, -1.55),
}
_CURLED = {6: (-0.25, -1.20), 7: (-0.20, -0.95), 8: (-0.18, -0.75),
           10: (0.02, -1.25), 11: (0.03, -0.98), 12: (0.03, -0.78),
           14: (0.22, -1.18), 15: (0.22, -0.92), 16: (0.21, -0.74),
           18: (0.42, -1.05), 19: (0.40, -0.85), 20: (0.38, -0.70)}


def hand(pose: str = "open", cx: float = 0.5, cy: float = 0.55, size: float = 0.16,
         aspect: float = 4 / 3, pinch_gap: float = 0.05, rotate_deg: float = 0.0) -> list[list[float]]:
    """pose: open | pinch | peace | fist | point | halfpinch(gap). size: el boyu (görüntü yüksekliği oranı)."""
    pts = dict(_BASE)
    if pose in ("fist", "peace", "point"):
        curl = {"fist": (6, 7, 8, 10, 11, 12, 14, 15, 16, 18, 19, 20),
                "peace": (14, 15, 16, 18, 19, 20),
                "point": (10, 11, 12, 14, 15, 16, 18, 19, 20)}[pose]
        for k in curl:
            pts[k] = _CURLED[k]
        if pose == "fist":
            pts[4] = (-0.20, -0.80)  # başparmak katlı parmakların üstünde → uçlar yakın ama yumruk
    if pose in ("pinch", "halfpinch"):
        pts[6], pts[7], pts[8] = (-0.30, -1.30), (-0.28, -1.40), (-0.22, -1.32)
        gap = pinch_gap if pose == "pinch" else pinch_gap
        pts[3] = (-0.45, -1.00)
        pts[4] = (-0.22 - gap, -1.32 + gap * 0.2)
    rad = math.radians(rotate_deg)
    out = []
    unit = size / 1.05  # orta parmak kökü uzunluğu = size
    for i in range(21):
        x, y = pts[i]
        xr = x * math.cos(rad) - y * math.sin(rad)
        yr = x * math.sin(rad) + y * math.cos(rad)
        # izotropik → normalize görüntü (x genişliğe göre)
        out.append([cx + xr * unit / aspect, cy + yr * unit + size, 0.0])
    return out


def frame(*hands_lm, score: float = 0.95) -> list[dict]:
    return [{"lm": lm, "score": score} for lm in hands_lm]
