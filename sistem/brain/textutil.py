"""Türkçe/İngilizce metin yardımcıları: katlama, sözcüklere ayırma, kök kırpma."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right

_TR_UPPER = str.maketrans({"I": "ı", "İ": "i"})


def lower_tr(text: str) -> str:
    return str(text or "").translate(_TR_UPPER).lower()


def fold(text: str) -> str:
    """Büyük/küçük harf ve aksanları kaldırır: 'Bütçe İŞİ' -> 'butce isi'."""
    value = unicodedata.normalize("NFKD", lower_tr(text).replace("ı", "i"))
    return "".join(ch for ch in value if not unicodedata.combining(ch))


_STOP_RAW = """
acaba ama ancak artık aslında az bana bazen bazı belki ben beni benim bir biraz birçok
birkaç birşey biz bize bizi bizim bu buna bunda bundan bunlar bunları bunların bunu bunun
burada böyle çok çünkü da daha dahi de defa değil diye diğer dolayı dolayısıyla en fakat
gibi göre hala hangi hatta hem hep hepsi her herhangi herkes hiç için ile ilgili ise işte
itibaren kadar karşın kendi kendine ki kim kimse mı mi mu mü nasıl ne neden nerde nerede
nereye niye niçin o olan olarak oldu olduğu olduğunu olmak olması olup olur on ona ondan
onlar onları onların onu onun orada öyle pek rağmen sadece sanki şey şeyi şimdi şöyle şu
şuna şunda şundan şunu tarafından tüm üzere var vardı ve veya ya yani yapılan yapmak
yaptı yine yok yoksa zaten ayrıca bile bizim gerek gibi iki üç dört beş altı yedi sekiz
dokuz sonra önce olarak olacak olabilir olduğunda yeni ilk son göster notları notlar not
dosya dosyalar dosyası klasör proje projesi projeyle projede projeler nedir hakkında
the and for are but not you all any can had her was one our out has have been this that
with from they will would there their what about which when your into more some than then
them these those only also just like over such very other how its may use using used
here where after before because while each should could does did done make made get got
http https www com org net html md txt png jpg jpeg pdf todo xlsx xls docx doc pptx ppt csv json
yaml yml toml py js ts swift heic gif svg mp3 mp4 mov zip
"""
STOPWORDS = frozenset(fold(w) for w in _STOP_RAW.split())

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def stem(folded_token: str) -> str:
    """Sabit önek kırpma (F5): Türkçe eklemeli yapıda bilgi erişiminde etkili bir yöntem."""
    return folded_token[:5] if len(folded_token) > 5 else folded_token


def iter_tokens(text: str):
    """(yüzey_biçimi, katlanmış, kök) üçlülerini üretir; durak sözcükleri atlar."""
    for match in _WORD_RE.finditer(lower_tr(text)):
        surface = match.group(0)
        folded = fold(surface)
        if len(folded) < 3 or len(folded) > 40 or folded.isdigit():
            continue
        if folded in STOPWORDS:
            continue
        if sum(ch.isdigit() for ch in folded) > len(folded) // 2:
            continue
        yield surface, folded, stem(folded)


_TOKEN_CACHE: dict[str, tuple | bool] = {}


def tokens_with_offsets(low_text: str):
    """lower_tr uygulanmış metinden (konum, yüzey, katlanmış, kök) üretir (önbellekli)."""
    for match in _WORD_RE.finditer(low_text):
        surface = match.group(0)
        info = _TOKEN_CACHE.get(surface)
        if info is None:
            folded = fold(surface)
            ok = (3 <= len(folded) <= 40 and not folded.isdigit() and folded not in STOPWORDS
                  and sum(ch.isdigit() for ch in folded) <= len(folded) // 2)
            info = (folded, stem(folded)) if ok else False
            if len(_TOKEN_CACHE) < 300_000:
                _TOKEN_CACHE[surface] = info
        if info:
            yield match.start(), surface, info[0], info[1]


def query_terms(query: str) -> list[str]:
    """Kullanıcı sorgusundan anlamlı katlanmış terimler (durak sözcükler hariç)."""
    return [folded for _, folded, _ in iter_tokens(query)][:8]


class LineIndex:
    """Karakter konumundan satır numarasına hızlı dönüşüm."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0]
        pos = text.find("\n")
        while pos != -1:
            self.starts.append(pos + 1)
            pos = text.find("\n", pos + 1)

    def line_of(self, offset: int) -> int:
        return bisect_right(self.starts, max(0, offset))  # 1 tabanlı

    def line_text(self, line_no: int, limit: int = 180) -> str:
        i = max(1, line_no) - 1
        if i >= len(self.starts):
            return ""
        start = self.starts[i]
        end = self.starts[i + 1] - 1 if i + 1 < len(self.starts) else len(self.text)
        line = " ".join(self.text[start:end].split())
        return line if len(line) <= limit else line[: limit - 1] + "…"

    def evidence(self, offset: int, limit: int = 180) -> tuple[int, str]:
        line_no = self.line_of(offset)
        return line_no, self.line_text(line_no, limit)
