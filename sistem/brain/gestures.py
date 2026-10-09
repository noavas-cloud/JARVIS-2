"""El hareketi tanıma motoru (saf Python; kamera ve arayüzden bağımsız, test edilebilir).

Girdi: MediaPipe el iskeleti (21 nokta, görüntüye göre 0..1, aynalanmış görüntü).
Çıktı: grafiğe uygulanacak olaylar + ekranda gösterilecek durum.

Yanlışlıkla işlem başlamasın diye:
  * El, en az ARM kare boyunca yeterli güven ve boyutla görünmeden hiçbir şey yapılmaz.
  * Sıkıştırma (başparmak+işaret) eşik altında art arda birkaç kare sürmeli; bırakma için
    daha yüksek bir eşik kullanılır (histerezis) → titreşim açıp kapatmaz.
  * Yumruk sıkıştırma sayılmaz (işaret parmağı katlıysa).
  * İki elle yakınlaştırmada ölü bölge ve kare başına en fazla değişim sınırı vardır.
  * ✌ ile sıfırlama AÇIK (04.10.2026 kullanıcı isteği; önceden kapalıydı): ✌ ~1 sn sabit tutulmalı; ilerleme
    gösterilir, tek sefer tetiklenir, ardından bekleme süresi uygulanır. Klavye (0) ve ⟲ SIFIRLA da sıfırlar.
  * Takip kaybolursa tutulan düğüm bulunduğu yerde bırakılır; başka işlem yapılmaz.
  * Tek elle iki kez hızlı yumruk (yumruk → açık el → yumruk, ~1,2 sn içinde) = "unzoom" olayı:
    yakınlaştırmadan/odaktan çıkar, SIFIRLAMAZ. Her yumruk ve aradaki açılış birkaç kare sürmeli;
    tek yumruk, yavaş ya da uzun tutulan yumruk tetiklemez. Dizi sürerken o elde sıkıştırma
    kazanılmaz (yumruk açılırken parmaklar yaklaşıp yanlışlıkla tutuş/çift sıkıştırma olmasın).
  * Yumrukla kaydırma (04.10.2026 kullanıcı isteği: "çok yakınlaşınca hep bir dosyaya dokunuyorum"): yumruk
    yapılıp el gezdirilince grafik kayar, hiçbir düğüm tutulmaz ("fist_pan_begin/fist_pan/fist_pan_end").
    El yumrukta biraz (fist_pan_start) kıpırdamadan başlamaz → yerinde yapılan çift yumruk bozulmaz; belirgin
    sürüklemeden (fist_pan_cancel) sonra çift yumruk dizisi baştan başlar (aç-kapa ile uzun kaydırma
    "yakınlaştırmadan çık" sayılmasın).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

WRIST, THUMB_TIP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP, RING_TIP, PINKY_TIP = 0, 4, 8, 9, 12, 16, 20
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
                    (18, 19), (19, 20), (0, 17)]


@dataclass
class GestureParams:
    arm_frames: int = 4          # el bu kadar kare görünmeden etkin olmaz
    # Not: takipçinin verdiği "score" MediaPipe'ın SAĞ/SOL el sınıflandırma güvenidir, elin görülme güveni değil (o eşik
    # MediaPipe içinde uygulanır). Yan dönük / avuç tersi / diğer ele yakın ikinci elde bu değer sık sık 0,5'in altına
    # düşüyor ve el atılıyordu ("ikinci elimi çok zor görüyor", 04.10) → yalnız çok düşük olanlar atılır.
    min_score: float = 0.20
    ghost_dist: float = 0.35     # iki algılamanın avuçları el boyunun bu katından yakınsa aynı el (hayalet kopya)
    min_span: float = 0.05       # bilek→orta parmak kökü (görüntü yüksekliği oranı); uzak/küçük eller yok sayılır
    pinch_in: float = 0.27       # başparmak-işaret uç mesafesi / el boyu: bunun altı = sıkıştırma adayı
    #                              (03.10 kullanıcının gerçek sıkıştırmaları 0,14–0,19 → .30'dan .27'ye: yanlış tetiklemeye pay)
    pinch_out: float = 0.50      # bunun üstü = bırakma (histerezis; tutarken tek karelik açılma bırakmasın)
    pinch_earn: int = 3          # sıkıştırma için art arda kare
    release_frames: int = 3      # bırakma için art arda kare
    min_index_ext: float = 0.90  # işaret parmağı bundan kısa (katlı) ise yumruktur, sıkıştırma değildir
    #                              (03.10 gerçek kayıtta 0,92'lik sıkı sıkıştırma kaçırıldı → .90)
    #                              (1.10'du: sıkı sıkıştırmada işaret parmağı bükülünce gerçek sıkıştırma reddediliyordu;
    #                               yumruk ayrıca poz "fist" ile dışlanır)
    pinch_smooth: float = 0.5    # bırakma kararı yumuşatılmış orana bakar (yeni kare ağırlığı): sürüklerken titreme
    #                              tutuşu düşürmesin. Başlama ham orana bakar (çift sıkıştırma hızlı kalsın).
    onset_max_speed: float = 2.5  # el bundan hızlı hareket ederken (el boyu/sn) yeni sıkıştırma başlamaz…
    # …ama parmaklar AÇIKÇA kapalıysa (04.10, gerçek kayıt: "hızlı" diye reddedilen 10 denemenin oranı 0,12-0,31)
    # hareket hâlinde de tutulur: oran ≤ fast_clear_pinch, hız < fast_max_speed, art arda fast_earn kare.
    depth_tolerance: float = 0.25  # parmak uçları arası derinlik farkının bu kadarı (el boyu oranı) gürültü sayılır
    fast_clear_pinch: float = 0.22
    fast_max_speed: float = 6.0
    fast_earn: int = 4
    #                               (hızlı el sallarken bulanık kareler yanlışlıkla sıkıştırma sanılıyordu)
    lost_grace: float = 0.30     # takip bu süreden uzun kaybolursa bırak
    zoom_deadzone: float = 0.04  # log(mesafe oranı) ölü bölgesi
    zoom_max_step: float = 0.10  # kare başına en fazla log değişim
    reset_gesture: bool = True   # ✌ ~1 sn sabit = sıfırla (04.10 kullanıcı isteği)
    reset_hold: float = 1.0      # ✌ tutma süresi (sn)
    reset_grace: float = 0.15    # ✌ anlık kaçırmalara tolerans (sn)
    reset_cooldown: float = 2.5
    reset_still: float = 0.08    # tutuş boyunca izin verilen hareket (normalize)
    double_fist: bool = True     # tek elle iki kez hızlı yumruk = yakınlaştırmadan çık (sıfırlamaz)
    fist_earn: int = 2           # yumruk sayılması için art arda kare
    fist_open_earn: int = 2      # iki yumruk arasındaki açılış için art arda kare
    fist_open_fingers: int = 3   # açılış: en az bu kadar parmak belirgin açık
    double_fist_s: float = 1.2   # ilk yumruktan ikincisine en fazla süre
    fist_cooldown: float = 1.5   # tetiklendikten sonra bekleme
    fist_pinch_guard: float = 0.3  # yumruk açıldıktan sonra bu kadar süre sıkıştırma kazanılmaz
    fist_pan: bool = True        # yumruk + el hareketi = grafiği kaydır (düğüme dokunmaz)
    fist_pan_start: float = 0.035  # kaydırma, yumruktaki imleç bu kadar (ekran oranı) kıpırdayınca başlar
    fist_pan_cancel: float = 0.08  # bu kadar sürüklenince yumruk çift yumruk dizisinden sayılmaz
    amp: float = 1.35            # merkezdeki rahat el alanı tüm ekrana ulaşsın
    slot_match: float = 0.30     # el kimliği eşleştirme mesafesi
    min_cutoff: float = 0.8      # One Euro filtresi: dururken güçlü yumuşatma (titreme yok)…
    beta: float = 8.0            # …hızlı harekette hızla açılan kesim frekansı (gecikme yok)
    stable_anchor: bool = True   # imleç avuç merkezinden sürülür; parmak kapanınca kaymaz
    offset_rate: float = 0.12    # parmak ucu–avuç farkının serbestken yavaş takibi (kare başına)
    freeze_ratio: float = 0.62   # parmaklar bu orandan yakınsa fark dondurulur (sıkıştırma kayması yok)
    deadband: float = 0.0012     # dururken bu kadar küçük oynamalar yok sayılır (≈1–2 piksel)
    extended: float = 1.45
    curled: float = 1.20


class OneEuro:
    """Casiez vd. One Euro filtresi: dururken titremeyi, hızlı harekette gecikmeyi azaltır."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.t = None
        self.x = None
        self.dx = 0.0

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.t = None
        self.x = None
        self.dx = 0.0

    def __call__(self, x: float, t: float) -> float:
        if self.t is None or self.x is None:
            self.t, self.x = t, x
            return x
        dt = min(0.2, max(1e-3, t - self.t))
        dx = (x - self.x) / dt
        a_d = self._alpha(dt, self.d_cutoff)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(dt, cutoff)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


def _dist(a, b, aspect):
    return math.hypot((a[0] - b[0]) * aspect, a[1] - b[1])


def _dist3(a, b, aspect):
    """Derinlik dahil uzaklık: el kameraya yan dönükken parmaklar üst üste görünse de ayrık sayılır.
    MediaPipe z değeri x ile aynı ölçektedir (görüntü genişliği)."""
    dz = ((a[2] if len(a) > 2 else 0.0) - (b[2] if len(b) > 2 else 0.0)) * aspect
    return math.sqrt(((a[0] - b[0]) * aspect) ** 2 + (a[1] - b[1]) ** 2 + dz * dz)


ANCHOR_POINTS = (0, 5, 9, 13, 17)   # bilek + dört parmak kökü: parmak hareketinden etkilenmez


def hand_metrics(lm, aspect: float = 1.0, params: GestureParams | None = None) -> dict:
    p = params or GestureParams()
    span = max(1e-4, _dist(lm[WRIST], lm[MIDDLE_MCP], aspect))
    ext = [_dist(lm[i], lm[WRIST], aspect) / span for i in (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)]
    span3 = max(1e-4, _dist3(lm[WRIST], lm[MIDDLE_MCP], aspect))
    # 04.10 (kullanıcı onayı, holo-gestures'tan): parmak uçları arasındaki DERİNLİK farkının küçük kısmı (el boyunun
    # depth_tolerance katına kadar = MediaPipe z gürültüsü) yok sayılır → pratikte 2B ölçüm; parmaklar değse bile z
    # gürültüsü oranı büyütüp tutuşu kaçırıyordu. Derinlikte AÇIKÇA ayrı uçlar (başparmak işaretin arkasında, ekranda
    # üst üste) yine sıkıştırma sayılmaz (29.09 koruması). El boyu (payda) 3B kalır. Eşik sayıları değişmedi.
    d2 = _dist(lm[THUMB_TIP], lm[INDEX_TIP], aspect)
    dz = (max(0.0, abs(lm[THUMB_TIP][2] - lm[INDEX_TIP][2]) * aspect - p.depth_tolerance * span3)
          if len(lm[THUMB_TIP]) > 2 and len(lm[INDEX_TIP]) > 2 else 0.0)     # z, x ölçeğinde (bkz. _dist3)
    pinch = math.hypot(d2, dz) / span3
    if all(e > p.extended for e in ext):
        pose = "open"
    elif ext[0] > p.extended and ext[1] > p.extended and ext[2] < p.curled and ext[3] < p.curled:
        pose = "peace"
    elif ext[0] > p.extended and all(e < p.curled for e in ext[1:]):
        pose = "point"
    elif all(e < 1.10 for e in ext):
        pose = "fist"
    else:
        pose = "other"
    return {
        "span": span, "ext": ext, "pinch": pinch, "pose": pose,
        "pinch_ok": pinch < p.pinch_in and ext[0] >= p.min_index_ext and pose != "fist",
        "thumb_tip_vs_dip": _dist(lm[THUMB_TIP], lm[INDEX_TIP], aspect)
                            / max(1e-4, _dist(lm[THUMB_TIP], lm[INDEX_TIP - 1], aspect)),
        "cursor": ((lm[THUMB_TIP][0] + lm[INDEX_TIP][0]) / 2, (lm[THUMB_TIP][1] + lm[INDEX_TIP][1]) / 2),
        "palm": ((lm[WRIST][0] + lm[MIDDLE_MCP][0]) / 2, (lm[WRIST][1] + lm[MIDDLE_MCP][1]) / 2),
        "anchor": (sum(lm[i][0] for i in ANCHOR_POINTS) / len(ANCHOR_POINTS),
                   sum(lm[i][1] for i in ANCHOR_POINTS) / len(ANCHOR_POINTS)),
        "edge": min(min(pt[0], 1.0 - pt[0], pt[1], 1.0 - pt[1]) for pt in lm[:21]),
    }


@dataclass
class HandSlot:
    index: int
    present: bool = False
    seen: int = 0
    last_seen: float = -1e9
    palm: tuple = (0.5, 0.5)
    cursor: tuple = (0.5, 0.5)
    pose: str = ""
    pinching: bool = False
    pinch_count: int = 0
    pinch_fast: bool = False       # bu tutuş adayı hareket hâlinde (net sıkıştırma yoluyla) sayılıyor
    release_count: int = 0
    blocked: bool = False          # iki el yakınlaştırmasından sonra, bırakana kadar yeni tutuş yok
    pressed: bool = False          # tek el tutuş/kaydırma olayı başlatıldı mı
    peace_since: float | None = None
    peace_last: float = -1e9
    peace_anchor: tuple | None = None
    peace_fired: bool = False
    fist_on: bool = False          # şu an kazanılmış bir yumruk var
    fist_count: int = 0
    fist_open_count: int = 0
    fist_first: float | None = None   # dizinin ilk yumruğunun zamanı
    fist_opened: bool = False      # ilk yumruktan sonra el açıldı mı
    fist_off_at: float = -1e9      # son yumruğun açıldığı an
    fist_guard: bool = False       # sıkıştırma kazanımı geçici olarak kapalı
    fist_seen_at: float = -1e9     # elin en son yumruk pozunda görüldüğü an
    fist_panning: bool = False     # yumrukla kaydırma sürüyor
    fist_pan_anchor: tuple | None = None   # yumruğun kazanıldığı andaki imleç (başlama eşiği için)
    fist_pan_last: tuple | None = None
    fist_pan_path: float = 0.0     # kaydırmada katedilen yol (ekran oranı)
    metrics: dict = field(default_factory=dict)
    landmarks: list = field(default_factory=list)
    fx: OneEuro | None = None
    fy: OneEuro | None = None
    offset: tuple | None = None    # parmak ucu noktası − avuç merkezi (yavaş izlenir)
    raw: tuple = (0.5, 0.5)        # eşlemeden önceki imleç noktası (kamera görüntüsünde)
    score: float = 1.0
    pinch_s: float | None = None   # yumuşatılmış sıkıştırma oranı
    speed: float = 0.0             # avucun hızı (el boyu/sn, yumuşatılmış)
    prev_palm: tuple | None = None
    prev_t: float = 0.0
    near_logged: float = -1e9      # en son "az kalsın" teşhis kaydı

    @property
    def armed(self) -> bool:
        return self.present and self.seen >= 1


class GestureEngine:
    def __init__(self, params: GestureParams | None = None):
        self.p = params or GestureParams()
        self.slots = [HandSlot(0), HandSlot(1)]
        for s in self.slots:
            s.fx = OneEuro(self.p.min_cutoff, self.p.beta)
            s.fy = OneEuro(self.p.min_cutoff, self.p.beta)
        self.mode = "idle"            # idle | press | zoom
        self.zoom_d0 = 0.0
        self.zoom_applied = 0.0
        self.zoom_total = 1.0
        self.last_reset = -1e9
        self.diag_counts = {"frames": 0, "raw2": 0, "valid2": 0, "ghost": 0}
        self.reset_progress = 0.0
        self.last_unzoom = -1e9
        self.diag: list[str] = []      # sıkıştırma teşhis satırları (pop_diag)

    # ── yardımcılar ───────────────────────────────────────────────────────
    def _map(self, x: float, y: float) -> tuple[float, float]:
        a = self.p.amp
        return (min(1.0, max(0.0, 0.5 + (x - 0.5) * a)), min(1.0, max(0.0, 0.5 + (y - 0.5) * a)))

    def _dedupe(self, hands: list[dict], aspect: float) -> list[dict]:
        """MediaPipe bazen TEK eli üst üste İKİ el diye bildirir (hayalet kopya); ikinci el eşiği düşürülünce bu
        kopya ikinci yuvaya oturup yakınlaştırmayı bozmasın: avuçları çok yakın iki algılamadan güveni yüksek
        olan kalır. Gerçek iki el (yan yana bile) avuç arası en az bir el boyu kadar uzaktır."""
        if len(hands) < 2:
            return hands
        a, b = hands[0], hands[1]
        ma, mb = hand_metrics(a["lm"], aspect, self.p), hand_metrics(b["lm"], aspect, self.p)
        d = math.hypot((ma["palm"][0] - mb["palm"][0]) * aspect, ma["palm"][1] - mb["palm"][1])
        if d < self.p.ghost_dist * max(1e-4, min(ma["span"], mb["span"])):
            self.diag_counts["ghost"] += 1
            keep = a if float(a.get("score", 1.0)) >= float(b.get("score", 1.0)) else b
            return [keep] + list(hands[2:])
        return hands

    def pop_counts(self) -> dict:
        """İki el teşhis sayaçları (İkinci Beyin günlüğüne 30 sn'de bir yazılır) ve sıfırlama."""
        out, self.diag_counts = self.diag_counts, {"frames": 0, "raw2": 0, "valid2": 0, "ghost": 0}
        return out

    def _assign(self, hands: list[dict], aspect: float) -> list:
        """Her algılamayı son avuç konumu en yakın yuvaya bağlar (MediaPipe sırası değişebilir)."""
        cands = []
        for h in hands[:2]:
            m = hand_metrics(h["lm"], aspect, self.p)
            cands.append((h, m))
        out = [None, None]
        used = set()
        # Toplam mesafesi en küçük eşleştirme (açgözlü değil): iki elden biri birkaç kare
        # kaybolunca kalan el yanlış yuvaya bağlanıp yakınlaştırmayı bozuyordu.
        pairs = []
        for s in self.slots:
            if not s.present:
                continue
            for k, (h, m) in enumerate(cands):
                d = math.hypot(m["palm"][0] - s.palm[0], m["palm"][1] - s.palm[1])
                if d < self.p.slot_match:
                    pairs.append((s.index, k, d))
        best_sel, best_key = [], (0, 0.0)
        for mask in range(1, 1 << len(pairs)):
            sel = [pairs[i] for i in range(len(pairs)) if mask >> i & 1]
            if len({a for a, _, _ in sel}) < len(sel) or len({b for _, b, _ in sel}) < len(sel):
                continue
            key = (-len(sel), sum(d for _, _, d in sel))
            if not best_sel or key < best_key:
                best_sel, best_key = sel, key
        for slot_index, k, _ in best_sel:
            out[slot_index] = cands[k]
            used.add(k)
        for k, c in enumerate(cands):
            if k in used:
                continue
            free = next((s.index for s in self.slots if out[s.index] is None and not s.present), None)
            if free is None:
                free = next((s.index for s in self.slots if out[s.index] is None), None)
            if free is not None:
                out[free] = c
                used.add(k)
        return out

    def _diag(self, kind: str, slot: HandSlot, m: dict, ratio: float):
        """Sıkıştırma teşhis satırı (pencere bunu İkinci Beyin günlüğüne yazar; görüntü yazılmaz, yalnız imlecin kaba
        konumu 0-1 ve elin kadraj kenarına uzaklığı — köşelerdeki kaçırmaları ayırt etmek için, 04.10).
        Kullanıcının gerçek elinden eşik ayarı için: ham/yumuşak oran, işaret uzanımı, hız, başparmak ucu/eklem oranı."""
        self.diag.append(f"sıkıştırma {kind} el={slot.index} oran={m['pinch']:.2f} yumuşak={ratio:.2f} "
                         f"işaret={m['ext'][0]:.2f} hız={slot.speed:.1f} uç/eklem={m.get('thumb_tip_vs_dip', 0):.2f} "
                         f"poz={m['pose']} boy={m['span']:.3f} imleç={slot.cursor[0]:.2f},{slot.cursor[1]:.2f} "
                         f"kenar={m.get('edge', 0):.3f}")
        del self.diag[:-50]

    def pop_diag(self) -> list:
        out, self.diag = self.diag, []
        return out

    # ── ana güncelleme ────────────────────────────────────────────────────
    def update(self, hands: list[dict], t: float, aspect: float = 4 / 3) -> dict:
        """hands: [{'lm': [[x,y,z]*21], 'score': float}], t: saniye. Olay listesi ve HUD durumu döner."""
        p = self.p
        events: list[tuple] = []
        valid = [h for h in hands if float(h.get("score", 1.0)) >= p.min_score and len(h.get("lm", [])) >= 21]
        valid = self._dedupe(valid, aspect)
        self.diag_counts["frames"] += 1
        self.diag_counts["raw2"] += len(hands) >= 2
        self.diag_counts["valid2"] += len(valid) >= 2
        assigned = self._assign(valid, aspect)
        for slot, item in zip(self.slots, assigned):
            if item is None:
                if slot.present and t - slot.last_seen > p.lost_grace:
                    self._lose(slot, t, events)
                continue
            h, m = item
            if m["span"] < p.min_span:  # çok uzaktaki/küçük el (ör. arkadaki biri)
                if slot.present and t - slot.last_seen > p.lost_grace:
                    self._lose(slot, t, events)
                continue
            if not slot.present:
                slot.present, slot.seen = True, 0
                slot.fx.reset()
                slot.fy.reset()
                slot.offset = None
                slot.pinch_s, slot.prev_palm, slot.speed = None, None, 0.0
            # Yumuşatılmış sıkıştırma oranı ve avuç hızı (el boyuna göre, kamera uzaklığından bağımsız)
            a = p.pinch_smooth
            slot.pinch_s = m["pinch"] if slot.pinch_s is None else slot.pinch_s + a * (m["pinch"] - slot.pinch_s)
            if slot.prev_palm is not None and t > slot.prev_t:
                v = math.hypot((m["palm"][0] - slot.prev_palm[0]) * aspect, m["palm"][1] - slot.prev_palm[1])
                v = v / max(1e-4, m["span"]) / (t - slot.prev_t)
                slot.speed += 0.5 * (v - slot.speed)
            slot.prev_palm, slot.prev_t = m["palm"], t
            slot.seen += 1
            slot.last_seen = t
            slot.palm = m["palm"]
            slot.metrics = m
            slot.pose = m["pose"]
            slot.landmarks = h["lm"]
            slot.score = float(h.get("score", 1.0))
            slot.raw = self._raw_point(slot, m)
            cx, cy = self._map(*slot.raw)
            fx, fy = slot.fx(cx, t), slot.fy(cy, t)
            if slot.seen > 1 and math.hypot(fx - slot.cursor[0], fy - slot.cursor[1]) < p.deadband:
                fx, fy = slot.cursor          # dururken piksel titremesini bastır
            slot.cursor = (fx, fy)
            self._pinch_state(slot, m, events)

        self._two_hand(t, events)
        self._single_hand(events)
        if self.p.double_fist or self.p.fist_pan:
            self._double_fist(t, events)
        if self.p.reset_gesture:
            self._reset_gesture(t, events)
        else:
            self.reset_progress = 0.0
        return {"events": events, "mode": self.mode, "zoom": self.zoom_total,
                "reset_progress": self.reset_progress, "hands": self.hud_hands()}

    def _raw_point(self, slot: HandSlot, m: dict) -> tuple[float, float]:
        """İmlecin kamera görüntüsündeki noktası.

        Başparmak–işaret ucu ortası, sıkıştırırken parmaklar kapandıkça kayar (düğüm tutulurken zıplar).
        Bu yüzden imleç parmak hareketinden etkilenmeyen avuç merkezinden sürülür; parmak ucuna olan
        fark serbestken yavaşça izlenir, parmaklar birbirine yaklaşınca ve tutarken dondurulur.
        """
        tip = m["cursor"]
        if not self.p.stable_anchor:
            return tip
        ax, ay = m["anchor"]
        want = (tip[0] - ax, tip[1] - ay)
        if slot.offset is None:
            slot.offset = want
        elif (not slot.pinching and not slot.fist_on and m["pose"] != "fist"
              and m["pinch"] > self.p.freeze_ratio):
            # Yumrukta da dondurulur: parmak ucu avuca kapanırken imleç kayıp kaydırma sanılmasın.
            a = self.p.offset_rate
            slot.offset = (slot.offset[0] + a * (want[0] - slot.offset[0]),
                           slot.offset[1] + a * (want[1] - slot.offset[1]))
        return ax + slot.offset[0], ay + slot.offset[1]

    def _lose(self, slot: HandSlot, t: float, events: list):
        if self.mode == "press" and slot.pressed:
            events.append(("release", slot.index, *slot.cursor, "lost"))
            self.mode = "idle"
        if self.mode == "zoom":
            events.append(("zoom_end",))
            self.mode = "idle"
            for s in self.slots:
                if s is not slot and s.pinching:
                    s.blocked = True
        slot.present = False
        slot.seen = 0
        slot.pinching = slot.pressed = False
        slot.pinch_count = slot.release_count = 0
        slot.blocked = False
        slot.peace_since = None
        slot.peace_fired = False
        self._end_fist_pan(slot, events)
        self._clear_fist(slot)
        events.append(("hand_lost", slot.index))

    def _pinch_state(self, slot: HandSlot, m: dict, events: list):
        p = self.p
        if slot.seen < p.arm_frames:
            slot.pinch_count = 0
            return
        ratio = m["pinch"] if slot.pinch_s is None else slot.pinch_s
        if not slot.pinching:
            shape = m["ext"][0] >= p.min_index_ext and m["pose"] != "fist"
            ok = shape and m["pinch"] < p.pinch_in and slot.speed < p.onset_max_speed
            fast = (shape and not ok and m["pinch"] <= p.fast_clear_pinch and slot.speed < p.fast_max_speed)
            if (ok or fast) and not slot.fist_guard:
                slot.pinch_count += 1
                slot.pinch_fast = slot.pinch_fast or fast
                if slot.pinch_count >= (p.fast_earn if slot.pinch_fast else p.pinch_earn):
                    slot.pinching = True
                    slot.release_count = 0
                    self._diag("tutus:hızlı_net" if slot.pinch_fast else "tutus", slot, m, ratio)
                    slot.pinch_fast = False
            else:
                slot.pinch_count = 0
                slot.pinch_fast = False
                # Teşhis: neredeyse sıkıştırma olup sayılmayan anlar (neden sayılmadığıyla), en çok 2 sn'de bir
                if m["pinch"] < p.pinch_in * 1.2 and slot.prev_t - slot.near_logged > 2.0:
                    slot.near_logged = slot.prev_t
                    why = ("hızlı" if slot.speed >= p.onset_max_speed else "yumruk" if m["pose"] == "fist"
                           else "işaret_katlı" if m["ext"][0] < p.min_index_ext
                           else "yumruk_sonrası" if slot.fist_guard else "eşik")
                    self._diag("az_kalsin:" + why, slot, m, ratio)
        else:
            if ratio > p.pinch_out:
                slot.release_count += 1
                if slot.release_count >= p.release_frames:
                    self._diag("birakma", slot, m, ratio)
                    slot.pinching = False
                    slot.pinch_count = 0
                    slot.blocked = False
                    if slot.pressed and self.mode == "press":
                        events.append(("release", slot.index, *slot.cursor, "open"))
                        self.mode = "idle"
                    slot.pressed = False
            else:
                slot.release_count = 0

    def _two_hand(self, t: float, events: list):
        a, b = self.slots
        both = a.present and b.present and a.pinching and b.pinching and not (a.blocked or b.blocked)
        if both and self.mode != "zoom":
            for s in (a, b):
                if s.pressed:
                    events.append(("release", s.index, *s.cursor, "zoom"))
                    s.pressed = False
            self.mode = "zoom"
            self.zoom_d0 = max(1e-3, math.hypot(a.cursor[0] - b.cursor[0], a.cursor[1] - b.cursor[1]))
            self.zoom_applied = 0.0
            self.zoom_total = 1.0
            events.append(("zoom_begin", (a.cursor[0] + b.cursor[0]) / 2, (a.cursor[1] + b.cursor[1]) / 2))
        elif self.mode == "zoom" and not (a.pinching and b.pinching and a.present and b.present):
            events.append(("zoom_end",))
            self.mode = "idle"
            for s in (a, b):
                if s.pinching:
                    s.blocked = True  # kalan el bırakmadan tutuş/kaydırma başlatmasın
        if self.mode == "zoom":
            d = max(1e-3, math.hypot(a.cursor[0] - b.cursor[0], a.cursor[1] - b.cursor[1]))
            lg = math.log(d / self.zoom_d0)
            dz = self.p.zoom_deadzone
            target = 0.0 if abs(lg) < dz else lg - math.copysign(dz, lg)
            step = max(-self.p.zoom_max_step, min(self.p.zoom_max_step, target - self.zoom_applied))
            if abs(step) > 1e-4:
                self.zoom_applied += step
                self.zoom_total = math.exp(self.zoom_applied)
                events.append(("zoom", math.exp(step), (a.cursor[0] + b.cursor[0]) / 2,
                               (a.cursor[1] + b.cursor[1]) / 2))

    def _single_hand(self, events: list):
        if self.mode == "zoom":
            return
        for s in self.slots:
            if not s.present:
                continue
            if s.pinching and not s.blocked and not s.pressed and self.mode == "idle":
                s.pressed = True
                self.mode = "press"
                events.append(("press", s.index, *s.cursor))
            elif s.pressed and self.mode == "press":
                events.append(("drag", s.index, *s.cursor))

    @staticmethod
    def _clear_fist(slot: HandSlot):
        slot.fist_on = slot.fist_opened = slot.fist_guard = False
        slot.fist_count = slot.fist_open_count = 0
        slot.fist_first = None
        slot.fist_panning = False
        slot.fist_pan_anchor = slot.fist_pan_last = None
        slot.fist_pan_path = 0.0

    @staticmethod
    def _end_fist_pan(slot: HandSlot, events: list):
        if slot.fist_panning:
            events.append(("fist_pan_end", slot.index))
        slot.fist_panning = False
        slot.fist_pan_anchor = slot.fist_pan_last = None
        slot.fist_pan_path = 0.0

    def _fist_pan(self, s: HandSlot, events: list):
        """Yumruk tutulurken el gezdirilince grafiği kaydırır (hiçbir düğüm tutulmaz). fist_on, el belirgin açılana
        kadar sürdüğü için tek karelik poz kaçırmaları kaydırmayı kesmez."""
        p = self.p
        if not (p.fist_pan and s.fist_on):
            self._end_fist_pan(s, events)
            return
        if s.pose != "fist":
            # Açılırken/tek karelik poz kaçırmasında kaydırma bekler (imleç parmak ucuna dönerken grafik sıçramasın);
            # son nokta güncellenmez → yeniden yumruk görülünce aradaki hareket de uygulanır.
            return
        cur = s.cursor
        if not s.fist_panning:
            if s.fist_pan_anchor is None:
                s.fist_pan_anchor = cur
            if math.hypot(cur[0] - s.fist_pan_anchor[0], cur[1] - s.fist_pan_anchor[1]) < p.fist_pan_start:
                return
            # Başlama noktası şimdiki imleç: eşik kadar kıpırdama grafiği bir anda zıplatmasın.
            s.fist_panning, s.fist_pan_last, s.fist_pan_path = True, cur, 0.0
            events.append(("fist_pan_begin", s.index, *cur))
            return
        step = math.hypot(cur[0] - s.fist_pan_last[0], cur[1] - s.fist_pan_last[1])
        s.fist_pan_path += step
        s.fist_pan_last = cur
        if s.fist_pan_path >= p.fist_pan_cancel and s.fist_first is not None:
            s.fist_first, s.fist_opened = None, False   # sürükleme: çift yumruk dizisinin ilk yumruğu sayılmaz
        if step > 0:
            events.append(("fist_pan", s.index, *cur))

    def _double_fist(self, t: float, events: list):
        """Tek elle iki kez hızlı yumruk → ("unzoom", el). Yalnızca boşta (tutuş/yakınlaştırma yokken)."""
        p = self.p
        for s in self.slots:
            if not (s.present and s.seen >= p.arm_frames and self.mode == "idle" and not s.pinching):
                self._end_fist_pan(s, events)
                self._clear_fist(s)
                continue
            ext = s.metrics.get("ext") or []
            if s.pose == "fist":
                s.fist_seen_at = t
                s.fist_count += 1
                s.fist_open_count = 0
            elif sum(1 for e in ext if e > p.curled) >= p.fist_open_fingers:
                s.fist_open_count += 1
                s.fist_count = 0
            else:                       # yarı açık geçiş karesi: yumruk sayılmaz, açılış da sayılmaz
                s.fist_count = 0
            if s.fist_first is not None and t - s.fist_first > p.double_fist_s:
                s.fist_first, s.fist_opened = None, False      # çok yavaş: dizi baştan başlar
            if not s.fist_on and s.fist_count >= p.fist_earn:
                s.fist_on = True
                if not p.double_fist:
                    pass                # yalnız yumrukla kaydırma açık: tek/çift yumruk olayı yok
                elif (s.fist_first is not None and s.fist_opened
                        and t - self.last_unzoom >= p.fist_cooldown):
                    events.append(("unzoom", s.index))
                    self.last_unzoom = t
                    s.fist_first, s.fist_opened = None, False
                else:
                    s.fist_first, s.fist_opened = t, False
                    # Tek yumruk (04.10): pencerede tek dokunuşla yapılan seçimi kaldırır; odak/yakınlaştırma
                    # için yine çift yumruk gerekir (ikinci yumruk aşağıda "unzoom" olur).
                    events.append(("fist", s.index))
            elif s.fist_on and s.fist_open_count >= p.fist_open_earn:
                s.fist_on = False
                s.fist_off_at = t
                if s.fist_first is not None:
                    s.fist_opened = True
            # Bekçi yalnız yumruk sırasında, yumruktan hemen sonra ve çift yumruk penceresinde açık.
            # (Eskiden fist_on'a bağlıydı: fist_on ancak 3 parmak açılınca kapanıyordu; yumruktan diğer parmaklar
            # kapalı hâlde doğrudan sıkıştırmaya geçen kullanıcıda bekçi saniyelerce açık kalıp tutuşları yutuyordu.)
            s.fist_guard = (s.pose == "fist" or t - s.fist_seen_at < p.fist_pinch_guard
                            or t - s.fist_off_at < p.fist_pinch_guard
                            or (s.fist_first is not None and t - s.fist_first <= p.double_fist_s))
            self._fist_pan(s, events)

    def _reset_gesture(self, t: float, events: list):
        p = self.p
        progress = 0.0
        for s in self.slots:
            eligible = (s.present and s.seen >= p.arm_frames and not s.pinching and self.mode == "idle")
            if eligible and s.pose == "peace":
                s.peace_last = t
                if s.peace_since is None or s.peace_anchor is None:
                    s.peace_since, s.peace_anchor = t, s.cursor
                moved = math.hypot(s.cursor[0] - s.peace_anchor[0], s.cursor[1] - s.peace_anchor[1])
                if moved > p.reset_still:  # el sabit değil: sayacı yeniden başlat
                    s.peace_since, s.peace_anchor = t, s.cursor
                held = t - s.peace_since
                prog = min(1.0, held / p.reset_hold)
                if not s.peace_fired:
                    progress = max(progress, prog)
                if prog >= 1.0 and not s.peace_fired and t - self.last_reset >= p.reset_cooldown:
                    s.peace_fired = True
                    self.last_reset = t
                    events.append(("reset",))
            elif s.peace_since is not None and eligible and t - s.peace_last <= p.reset_grace:
                if not s.peace_fired:  # kısa algılama kaçırması: sayaç korunur, ilerleme sayılmaz
                    progress = max(progress, min(1.0, (s.peace_last - s.peace_since) / p.reset_hold))
            else:
                s.peace_since = None
                s.peace_anchor = None
                s.peace_fired = False
        self.reset_progress = progress

    def hud_hands(self) -> list[dict]:
        out = []
        for s in self.slots:
            if not s.present:
                continue
            state = ("hazırlanıyor" if s.seen < self.p.arm_frames else
                     "sıkıştırıyor" if s.pinching else
                     "kaydırıyor" if s.fist_panning else
                     {"open": "açık el", "peace": "V", "fist": "yumruk", "point": "işaret"}.get(s.pose, "el"))
            ratio = s.metrics.get("pinch", 1.0)
            far = 0.9
            closeness = 1.0 if s.pinching else max(0.0, min(1.0, (far - ratio) / (far - self.p.pinch_in)))
            a = self.p.amp
            lo, hi = 0.5 - 0.5 / a, 0.5 + 0.5 / a
            out.append({"slot": s.index, "cursor": s.cursor, "pinching": s.pinching, "pose": s.pose,
                        "armed": s.seen >= self.p.arm_frames, "state": state, "blocked": s.blocked,
                        "pinch_ratio": round(ratio, 3), "closeness": round(closeness, 3),
                        "landmarks": s.landmarks, "raw": s.raw, "score": round(s.score, 3),
                        "span": round(s.metrics.get("span", 0.0), 4),
                        "edge": round(s.metrics.get("edge", 0.5), 4),
                        "in_zone": lo <= s.raw[0] <= hi and lo <= s.raw[1] <= hi,
                        "fist_pending": s.fist_first is not None, "fist_pan": s.fist_panning})
        return out

    def clear(self) -> list[tuple]:
        """El kontrolü kapatılırken güvenli bırakma olayları."""
        events = []
        if self.mode == "press":
            for s in self.slots:
                if s.pressed:
                    events.append(("release", s.index, *s.cursor, "disabled"))
        if self.mode == "zoom":
            events.append(("zoom_end",))
        self.__init__(self.p)
        return events
