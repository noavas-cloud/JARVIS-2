"""JARVIS 2 arayüz teması. Zemin rengi kürenin zemin rengiyle aynı olmalı (hud_render.BG = #0a1d23):
kürenin görüntüsünün kenarı zeminle birebir aynı renkte olduğu için kare hiç görünmez."""

BG = "#0a1d23"
PANEL = "#0c242b"
PANEL_HI = "#0f2c34"
EDGE = "#1b424c"
EDGE_HI = "#2a5f6c"
CYAN = "#5fd0f0"
TEAL = "#73c1cc"
GOLD = "#d6c052"
AMBER = "#e0a84a"
RED = "#ff4d5e"
MUTED_RED = "#cc2255"
VIOLET = "#ac8eff"
TEXT = "#d7eff4"
TEXT_DIM = "#7fa3ac"
TEXT_FAINT = "#4f7680"
BUBBLE_AI = "#10303a"
BUBBLE_YOU = "#2b2914"

BODY = "Grift"
DISPLAY = "Grift Extra Bold"


def body(size: int):
    return (BODY, size)


def bold(size: int):
    return (BODY, size, "bold")


def display(size: int):
    return (DISPLAY, size)


def mix(hex_a: str, hex_b: str, t: float) -> str:
    """İki rengin karışımı (t=0 → a, t=1 → b)."""
    t = max(0.0, min(1.0, t))
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def dim(color: str, t: float) -> str:
    """Rengi zemine doğru soldurur (Tk'de saydamlık yok)."""
    return mix(BG, color, t)


def tr_upper(text: str) -> str:
    return str(text).replace("i", "İ").replace("ı", "I").upper()
