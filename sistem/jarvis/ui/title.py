"""Kürenin ortasındaki "J.A.R.V.I.S." yazısı — kullanıcının fotoğrafındaki (Icon/jarvis_hud.webp) gibi.

Fotoğraftan ölçülenler (03.10): yazı yatayda çok geniş (Arial Bold ≈1,51× genişletilmiş), büyük harf yüksekliği
kürenin yarıçapının ~%9,4'ü, genişliği (son nokta dahil) ~%99'u; renk beyaza yakın camgöbeği (232, 249, 251), üst
kenarı biraz daha camgöbeği (198, 234, 245), çevresinde hafif camgöbeği ışıma.

Yazı küre ışık katmanının (resim) içine, 4 kat büyük çizilip küçültülerek (kenarları yumuşak) eklenir. Önce Tk
vektör çokgenleri denendi: macOS Tk'de dolgunun kenarı yumuşatılmıyor ve mantıksal piksel çözünürlüğünde
basamaklı görünüyordu. Yazı tipi yoksa eski (Tk) yazıya düşülür. Kürenin geri kalanı (hud_render.py) değişmedi.
Yazının parlaklığı durumla değişir (duraklatınca söner): hud_render'daki eski yazı rengiyle aynı oran.
"""

from __future__ import annotations

import functools

TEXT = "J.A.R.V.I.S."
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
STRETCH = 1.51                  # yatay genişletme (fotoğraftaki harf en/boy oranı)
CAP_R = 0.094                   # büyük harf yüksekliği / küre yarıçapı
Y_R = -0.02                     # yazının merkezi: cy + Y_R·R (eski yazıyla aynı yer)
TOP = (198, 234, 245)           # fotoğraftaki harf rengi: üst kenar
BODY = (232, 249, 251)          #                          gövde
GLOW = (95, 208, 240)           # ışıma rengi (kürenin camgöbeği)
SS = 4                          # kenar yumuşatma için büyük çizim katı


@functools.lru_cache(maxsize=1)
def _master():
    """Yazının yüksek çözünürlüklü maskesi (genişletilmemiş) ve büyük harf yüksekliği; olmazsa None."""
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(FONT, 400)
        img = Image.new("L", (4200, 700), 0)
        ImageDraw.Draw(img).text((40, 40), TEXT, font=font, fill=255)
        a = np.asarray(img)
        ys, xs = np.nonzero(a > 0)
        img = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        cap = float(ys.max() - ys.min() + 1)        # Arial'da J ve noktalar taban çizgisinde
        img = _photo_j(img, cap)
        cols = np.nonzero((np.asarray(img) > 0).any(axis=0))[0]
        img = img.crop((int(cols.min()), 0, int(cols.max()) + 1, img.height))
        return img, cap
    except Exception as exc:
        print(f"[KÜRE] Yazı hazırlanamadı ({exc}); eski yazı kullanılıyor.", flush=True)
        return None


def _photo_j(img, cap: float):
    """Arial'ın J'si yerine fotoğraftaki J: dik gövde + aynı kalınlıkta düz, kısa ayak, sağ alt köşe yuvarlak,
    sol ucu dik kesik. Arial J'nin kancası ince ve eğik bittiği için küçük boyda kopuk ("kesik") görünüyordu.
    Fotoğrafta J'nin eni (genişletilmiş) büyük harf yüksekliğinin ~0,72'si; gövde ve ayak I ile aynı kalınlıkta."""
    import numpy as np
    from PIL import ImageDraw
    a = np.asarray(img) > 127
    cols = a.any(axis=0)
    end = int(np.argmax(~cols[1:])) + 1             # J'den sonraki ilk boş sütun
    stem = np.nonzero(a[int(cap * 0.1), :end])[0]
    right, sw = int(stem.max()), int(stem.max() - stem.min() + 1)
    width = int(round(0.72 * cap / STRETCH))
    bottom, top = int(cap) - 1, 0
    rr = int(0.8 * sw)
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.rectangle((0, 0, end - 1, out.height), fill=0)                       # Arial J silinir
    left = right - width + 1
    d.rectangle((right - sw + 1, top, right, bottom - rr), fill=255)       # gövde
    d.rectangle((left, bottom - sw + 1, right - rr, bottom), fill=255)     # ayak
    d.rectangle((right - rr, bottom - rr, right, bottom - rr), fill=255)
    d.pieslice((right - 2 * rr, bottom - 2 * rr, right, bottom), 0, 90, fill=255)   # yuvarlak köşe
    d.rectangle((right - sw + 1, bottom - rr, right - rr, bottom), fill=255)        # köşe ile gövde/ayak arası
    return out


def available() -> bool:
    return _master() is not None


@functools.lru_cache(maxsize=8)
def _layers(size: int):
    """Bu küre boyutu için: yazı maskesi, ışıma maskesi, renk geçişi ve yerleşim kutusu."""
    from PIL import Image, ImageFilter
    import hud_render
    master, cap = _master()
    R = hud_render.R_FRAC * size
    k = CAP_R * R / cap                              # yazı ölçeği (mantıksal piksel / büyük çizim pikseli)
    tw, th = master.width * k * STRETCH, master.height * k
    pad = int(0.08 * R) + 2
    w, h = int(tw + 2 * pad) + 1, int(th + 2 * pad) + 1
    big = Image.new("L", (w * SS, h * SS), 0)
    sm = master.resize((max(1, int(round(tw * SS))), max(1, int(round(th * SS)))), Image.LANCZOS)
    ox, oy = (w * SS - sm.width) // 2, (h * SS - sm.height) // 2
    big.paste(sm, (ox, oy))
    mask = big.resize((w, h), Image.LANCZOS)
    glow = mask.filter(ImageFilter.GaussianBlur(max(1.5, 0.03 * R)))
    grad = Image.new("RGB", (1, h))
    top, bottom = (oy / SS), (oy + sm.height) / SS
    for y in range(h):
        t = min(1.0, max(0.0, (y - top) / max(1.0, 0.28 * (bottom - top))))   # üst kenar camgöbeği → gövde
        grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(TOP, BODY)))
    grad = grad.resize((w, h))
    cx, cy = size / 2, size / 2 + Y_R * R
    box = (int(round(cx - w / 2)), int(round(cy - h / 2)))
    return mask, glow, grad, box


def add_title(img, size: int, bright: float):
    """Küre ışık katmanına yazının IŞIMASINI ekler (harfler ayrı katmanda, halkaların üstünde: overlay)."""
    if not available():
        return img
    try:
        from PIL import Image, ImageChops
        import hud_render
        mask, glow, grad, box = _layers(size)
        w, h = mask.size
        region = img.crop((box[0], box[1], box[0] + w, box[1] + h))
        if region.mode != "RGB":
            region = region.convert("RGB")
        kg = 0.55 * min(1.0, max(0.0, bright))
        black = Image.new("RGB", (w, h), (0, 0, 0))
        lit = ImageChops.screen(region, Image.composite(Image.new("RGB", (w, h), GLOW), black,
                                                        glow.point(lambda v: int(v * kg))))
        out = img.copy()
        out.paste(lit if img.mode == "RGB" else lit.convert(img.mode), box)
        return out
    except Exception as exc:
        print(f"[KÜRE] Yazı eklenemedi: {exc}", flush=True)
        return img


@functools.lru_cache(maxsize=24)
def overlay(size: int, k: float):
    """Harfler: saydam zeminli RGBA resim + küre resmine göre sol üst köşe. Kürenin halka çizgilerinin ÜSTÜNE
    konur (eski Tk yazısı gibi); ışıklandırma katmanına çizilince iç halka "J"yi kesiyordu (03.10).
    k: parlaklık oranı (eski yazı renginden; duraklatınca söner) — 0,02 adımla önbelleğe alınır."""
    from PIL import Image
    import hud_render
    mask, _, grad, box = _layers(size)
    color = Image.blend(Image.new("RGB", mask.size, hud_render.BG), grad, max(0.0, min(1.0, k)))
    rgba = color.convert("RGBA")
    rgba.putalpha(mask)
    return rgba, box


def text_ratio(item_hex: str) -> float:
    """hud_render'ın yazı rengi (_mix(TEXT, k, BG)) → k (0..1), 0,02 adımla."""
    import hud_render
    r = int(item_hex[1:3], 16)
    k = (r - hud_render.BG[0]) / max(1, hud_render.TEXT[0] - hud_render.BG[0])
    return round(max(0.0, min(1.0, k)) * 50) / 50
