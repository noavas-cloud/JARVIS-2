"""Dosyalardan düz metin çıkarma. Yalnızca indeksleyici çağırır; ağ kullanmaz."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

NOTE_EXTS = {".md", ".markdown", ".mdown", ".mkd", ".txt", ".text", ".org", ".rst", ".adoc"}
PLAIN_EXTS = {
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".tex", ".bib",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".swift", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".m", ".go", ".rs", ".rb", ".php", ".sh", ".zsh", ".sql", ".css", ".scss", ".r", ".lua",
}
MARKUP_EXTS = {".html", ".htm", ".xml", ".svg"}
DOC_EXTS = {".docx", ".pptx", ".odt", ".odp", ".rtf", ".pdf"}
TEXT_BEARING = NOTE_EXTS | PLAIN_EXTS | MARKUP_EXTS | DOC_EXTS

MAX_PLAIN_BYTES = 2 * 1024 * 1024
MAX_DOC_BYTES = 40 * 1024 * 1024
MAX_CHARS = 200_000
PDF_MAX_PAGES = 40


def classify(path: Path) -> str:
    return "note" if path.suffix.lower() in NOTE_EXTS else "file"


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1254")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:4096]
    return b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff"))


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_BLOCK_RE = re.compile(r"</(p|div|li|h[1-6]|tr|br|section|article)>|<br\s*/?>", re.I)


def _strip_markup(text: str) -> str:
    text = _SCRIPT_RE.sub(" ", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024


def _zip_xml_text(path: Path, members: list[str], para_tag: str) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for name in members:
            if name not in names:
                continue
            if zf.getinfo(name).file_size > MAX_ZIP_MEMBER_BYTES:
                continue  # sıkıştırma bombası / aşırı büyük parça belleği doldurmasın
            xml = zf.read(name).decode("utf-8", errors="replace")
            xml = re.sub(rf"</{para_tag}>", "\n", xml)
            xml = _TAG_RE.sub("", xml)
            parts.append(html.unescape(xml))
    return "\n".join(line.strip() for line in "\n".join(parts).splitlines() if line.strip())


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    members = ["word/document.xml"] + sorted(n for n in names if re.match(r"word/(header|footer|footnotes)\d*\.xml", n))
    return _zip_xml_text(path, members, "w:p")


def _pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()

    def slide_no(name: str) -> int:
        m = re.search(r"(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=slide_no)
    return _zip_xml_text(path, slides, "a:p")


def _odf(path: Path) -> str:
    return _zip_xml_text(path, ["content.xml"], "text:p")


def _rtf(path: Path) -> str:
    tool = shutil.which("textutil")
    if tool:
        try:
            out = subprocess.run([tool, "-convert", "txt", "-stdout", str(path)],
                                 capture_output=True, timeout=10)
            if out.returncode == 0:
                return _decode(out.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    raw = _decode(path.read_bytes()[:MAX_PLAIN_BYTES])
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1254", "replace"), raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]", " ", raw)
    return "\n".join(" ".join(line.split()) for line in raw.splitlines() if line.strip())


# macOS'ta PDFKit, ek paket gerektirmeden JavaScript for Automation üzerinden kullanılır.
_PDF_JXA = r"""
ObjC.import('PDFKit'); ObjC.import('Foundation');
function run(argv) {
  var paths = JSON.parse(argv[0]), maxPages = Number(argv[1]), limit = Number(argv[2]), out = [];
  for (var i = 0; i < paths.length; i++) {
    var text = "";
    try {
      var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(paths[i]));
      if (doc && !doc.isNil()) {
        var n = Math.min(doc.pageCount, maxPages), chunks = [];
        for (var p = 0; p < n; p++) {
          var page = doc.pageAtIndex(p);
          if (page && !page.isNil()) {
            var s = page.string;
            if (s && !s.isNil()) chunks.push(ObjC.unwrap(s));
          }
          if (chunks.join("\n").length > limit) break;
        }
        text = chunks.join("\n").slice(0, limit);
      }
    } catch (e) { text = ""; }
    out.push(text);
  }
  return JSON.stringify(out);
}
"""


def extract_pdfs(paths: list[Path], timeout_per_file: float = 6.0,
                 failed: set | None = None) -> dict[str, str]:
    """PDF metinlerini toplu çıkarır. macOS dışında pypdf varsa onu dener.
    failed verilirse okunamayan (zaman aşımı/hata) yollar oraya eklenir; bunlar önbelleğe
    "metin yok" diye yazılmaz, sonraki dizinlemede yeniden denenir."""
    result: dict[str, str] = {}
    if not paths:
        return result
    if sys.platform == "darwin" and Path("/usr/bin/osascript").exists():
        for i in range(0, len(paths), 12):
            batch = paths[i:i + 12]
            try:
                out = subprocess.run(
                    ["/usr/bin/osascript", "-l", "JavaScript", "-e", _PDF_JXA,
                     json.dumps([str(p) for p in batch]), str(PDF_MAX_PAGES), str(MAX_CHARS)],
                    capture_output=True, text=True, timeout=timeout_per_file * len(batch) + 4)
                if out.returncode == 0:
                    texts = json.loads(out.stdout or "[]")
                    for p, t in zip(batch, texts):
                        if isinstance(t, str) and t.strip():
                            result[str(p)] = t
                elif failed is not None:
                    failed.update(str(p) for p in batch)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                if failed is not None:
                    failed.update(str(p) for p in batch)
                continue
        return result
    try:
        from pypdf import PdfReader  # isteğe bağlı; JARVIS gereksinimi değildir
    except Exception:
        return result
    for p in paths:
        try:
            reader = PdfReader(str(p))
            chunks = []
            for page in reader.pages[:PDF_MAX_PAGES]:
                chunks.append(page.extract_text() or "")
            text = "\n".join(chunks)[:MAX_CHARS]
            if text.strip():
                result[str(p)] = text
        except Exception:
            continue
    return result


def extract_text(path: Path) -> tuple[str, str]:
    """(metin, yöntem) döndürür. Metin yoksa ("", neden). PDF'ler toplu işlenir."""
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return "", "okunamadı"
    try:
        if ext in NOTE_EXTS or ext in PLAIN_EXTS or ext in MARKUP_EXTS:
            if size > MAX_PLAIN_BYTES:
                with path.open("rb") as fh:
                    raw = fh.read(MAX_PLAIN_BYTES)
            else:
                raw = path.read_bytes()
            if _looks_binary(raw):
                return "", "ikili dosya"
            text = _decode(raw)
            if ext in MARKUP_EXTS:
                text = _strip_markup(text)
            return text[:MAX_CHARS], "metin"
        if size > MAX_DOC_BYTES:
            return "", "çok büyük"
        if ext == ".docx":
            return _docx(path)[:MAX_CHARS], "docx"
        if ext == ".pptx":
            return _pptx(path)[:MAX_CHARS], "pptx"
        if ext in (".odt", ".odp"):
            return _odf(path)[:MAX_CHARS], "odf"
        if ext == ".rtf":
            return _rtf(path)[:MAX_CHARS], "rtf"
        if ext == ".pdf":
            return "", "pdf-toplu"
    except Exception as exc:
        # Bozuk/şifreli docx-odt (zlib.error, RuntimeError, NotImplementedError…) tüm dizini
        # durdurmasın; bu dosya atlanır, gerisi dizinlenir.
        return "", f"okunamadı: {type(exc).__name__}"
    return "", "metin içermiyor"
