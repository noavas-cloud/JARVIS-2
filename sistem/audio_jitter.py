"""Gemini sesi için küçük bir dalgalanma tamponu (jitter buffer).

Gemini sesi parça parça gelir; ortalamada gerçek zamandan hızlıdır ama parçalar arasında ara sıra kısa duraklar olur.
Bir cevabın ilk parçası gelir gelmez çalmaya başlanırsa ses kartının tamponunda yalnız o parça kadar ses olur ve
sonraki parça birkaç on ms geç kalınca hoparlörde milisaniyelik boşluklar ("küçük kesilmeler") duyulur. Bu boşluklar
yazmalar arası süreye bakan eski teşhiste görünmez (süre tampondan kısa ama tampon zaten neredeyse boştur).

Çözüm: cevabın başında ~0,2 sn ses biriktirip öyle başla; akış ortada kuruyup yeniden gelirse ~0,12 sn biriktir.
Beklemenin bir üst sınırı vardır (kısa cevaplar gecikmesin) ve bitiş işareti (None) gelince hemen çalınır.
"""

from __future__ import annotations

import asyncio

RATE = 24000
BYTES_PER_S = RATE * 2              # 16 bit mono
PREROLL_S = 0.20                    # cevabın başında biriktirilen ses
REPRIME_S = 0.12                    # akış ortada kuruyunca biriktirilen ses
REPRIME_AFTER_S = 0.05              # bir parçayı bu kadardan uzun beklediysek akış kurumuş say
MAX_WAIT_S = 0.30                   # biriktirirken en fazla bu kadar bekle


async def gather(queue: "asyncio.Queue", first: bytes, target_s: float, max_wait_s: float = MAX_WAIT_S):
    """first'ten başlayarak kuyruktan target_s saniyelik ses toplar. (birleşik_ses, ertelenenler) döndürür;
    ertelenenler, ses olmayan ilk öğedir (ör. bitiş işareti None) ve çağıran onu sıradaki öğe olarak işlemelidir."""
    parts = [first]
    total = len(first)
    target = int(target_s * BYTES_PER_S)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    held = []
    while total < target:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                break
        if not isinstance(item, (bytes, bytearray)):
            held.append(item)
            break
        parts.append(item)
        total += len(item)
    return b"".join(parts), held


class PlayClock:
    """Ses kartına yazılan sesin ne zaman tükeneceğini hesaplar (engelleyen yazmada PortAudio boşalmayı güvenle
    bildiremez: exception_on_underflow akışı kapatır). Yazma başlarken önceki ses çoktan bittiyse aradaki süre
    hoparlörde duyulan boşluktur."""

    def __init__(self, rate: int = RATE):
        self.bytes_per_s = rate * 2
        self.runout = None

    def reset(self):
        self.runout = None

    def before_write(self, now: float, nbytes: int) -> float:
        """Yazma başlamadan çağrılır; bu yazmadan önce oluşan boşluğu (sn) döndürür (cevabın ilk yazmasında 0)."""
        gap = 0.0
        if self.runout is not None and now > self.runout:
            gap = now - self.runout
        start = now if self.runout is None or now > self.runout else self.runout
        self.runout = start + nbytes / self.bytes_per_s
        return gap
