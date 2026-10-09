"""Read-only analysis of the actual foreground item or an explicit system/calendar target."""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit

from actions.calendar import _parse_payload, _run_helper
from actions.context_control import (CHROME_PAGE, CODE_EXTENSIONS, SAFARI_PAGE,
                                     _file_url_to_path, _jxa, _same_app, _screen_error, _swift,
                                     active_context)
from actions.performance_diagnosis import diagnose_slow_mac
from actions.screen_vision import analyze_screen


MAX_CODE_BYTES = 500_000
MAX_CODE_CHARS = 24_000
MAX_PDF_PAGES = 8
MAX_FOLDER_ITEMS = 500


def _reply(status: str, **fields) -> str:
    return json.dumps({"status": status, "changes_made": False, **fields}, ensure_ascii=False)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    return "".join(char for char in value if not unicodedata.combining(char))


def _system() -> str:
    data = json.loads(diagnose_slow_mac())
    data["kind"] = "system"
    return json.dumps(data, ensure_ascii=False)


def _document_path(context: dict) -> Path | None:
    raw = _file_url_to_path(context.get("document", ""))
    return Path(raw) if raw else None


def _selected_path(context: dict) -> Path | None:
    paths = context.get("selected_paths", [])
    return Path(paths[0]) if len(paths) == 1 else None


def _code(path: Path, context: dict) -> str:
    if not path.is_file() or path.is_symlink() or path.suffix.lower() not in CODE_EXTENSIONS:
        return _reply("error", message="Doğrulanmış bir kod dosyası seçili değil.")
    if not _same_app(context):
        return _reply("error", message="Aktif uygulama değişti; kod okunmadı.")
    try:
        if path.stat().st_size > MAX_CODE_BYTES:
            return _reply("partial", kind="code", path=str(path),
                          message="Kod dosyası güvenli okuma sınırından büyük; tam analiz yapılamadı.")
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _reply("error", message="Kod dosyası okunamadı.")
    syntax_issue = None
    if path.suffix.lower() == ".py":
        try:
            ast.parse(body, filename=str(path))
        except SyntaxError as exc:
            syntax_issue = {"line": exc.lineno, "message": exc.msg}
    return _reply("ok", kind="code", path=str(path), language=path.suffix.lower().lstrip("."),
                  text=body[:MAX_CODE_CHARS], truncated=len(body) > MAX_CODE_CHARS,
                  syntax_issue=syntax_issue,
                  guidance="Somut hata ve satırı belirt; sadece koddan kesinleşmeyen çalışma zamanı hatasını varsayma. Dosyayı değiştirme.")


def _sample_pages(page_count: int) -> list[int]:
    if page_count <= MAX_PDF_PAGES:
        return list(range(1, page_count + 1))
    return sorted({1 + round(i * (page_count - 1) / (MAX_PDF_PAGES - 1))
                   for i in range(MAX_PDF_PAGES)})


def _pdf(path: Path, context: dict) -> str:
    if path.suffix.lower() != ".pdf" or not path.is_file() or path.is_symlink():
        return _reply("error", message="Doğrulanmış PDF dosyası seçili değil.")
    if not _same_app(context):
        return _reply("error", message="Aktif uygulama değişti; PDF okunmadı.")
    first = _swift("pdf_page", str(path), "1")
    if first.get("status") != "ok":
        return _reply("error", message=first.get("message", "PDF okunamadı."))
    count = int(first.get("page_count", 0))
    pages = []
    for number in _sample_pages(count):
        data = first if number == 1 else _swift("pdf_page", str(path), str(number))
        if data.get("status") == "ok":
            pages.append({"page": number, "text": str(data.get("text", ""))[:4000],
                          "truncated": bool(data.get("truncated")) or len(str(data.get("text", ""))) > 4000})
    if not pages or not any(item["text"].strip() for item in pages):
        return _reply("partial", kind="pdf", path=str(path), page_count=count,
                      message="PDF'den metin çıkarılamadı; taranmış görüntü olabilir. Görünen sayfayı incelemek için ekran analizi gerekir.")
    return _reply("ok" if len(pages) == count else "partial", kind="pdf", path=str(path),
                  page_count=count, sampled_pages=[item["page"] for item in pages],
                  pages=pages, sampled=len(pages) < count,
                  guidance="Ana noktaları yalnızca okunan sayfalara dayandır; örneklenen PDF'nin tamamını okuduğunu söyleme.")


def _web(context: dict) -> str:
    app_id = context.get("bundle_id", "")
    if app_id == "com.apple.Safari":
        page = _jxa(SAFARI_PAGE, "text", timeout=12)
    else:
        name = "Google Chrome" if app_id == "com.google.Chrome" else "Microsoft Edge"
        page = _jxa(CHROME_PAGE, name, "text", timeout=12)
    if page.get("status") != "ok" or not page.get("text"):
        if not _same_app(context):
            return _reply("error", message="Aktif web sayfası değişti; analiz yapılmadı.")
        visible = analyze_screen("Aktif web sayfasında görünen kaynak, tarih, yazar ve iddiaları belirle; görünmeyeni uydurma.",
                                 "context_window")
        if _screen_error(visible):
            return _reply("error", message="Web sayfasının metni ve ekranı okunamadı.", detail=visible)
        return _reply("partial", kind="web", url=context.get("browser_url", ""),
                      message="Tam sayfa metni okunamadı; yalnızca görünen bölüm incelendi.", visible_analysis=visible)
    if not _same_app(context) or page.get("url") != context.get("browser_url"):
        return _reply("error", message="Aktif web sayfası değişti; analiz yapılmadı.")
    url = str(page.get("url", ""))
    text = str(page.get("text", ""))
    return _reply("ok", kind="web", url=url, host=urlsplit(url).hostname or "",
                  title=page.get("title", ""), text=text[:18000],
                  truncated=bool(page.get("truncated")) or len(text) > 18000,
                  independently_verified=False,
                  guidance="Güvenilirliği yazar, tarih, kanıt ve kaynak atıfları açısından değerlendir. Alan adına bakarak kesin güvenilir deme; bağımsız doğrulama yapılmadıysa bunu belirt.")


def _calendar(query: str) -> str:
    now = datetime.now().astimezone()
    folded = _fold(query)
    if "yarin" in folded:
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif "bugun" in folded:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    ok, raw = _run_helper("range", payload={"start_iso": start.isoformat(),
                                             "end_iso": end.isoformat()}, timeout=20)
    if not ok:
        return _reply("error", kind="calendar", message="Takvim okunamadı: " + str(raw)[:180])
    valid, detail, events = _parse_payload(raw)
    if not valid:
        return _reply("error", kind="calendar", message="Takvim okunamadı: " + detail[:180])
    events = sorted((event for event in events if event["end_ts"] >= now.timestamp()),
                    key=lambda item: (item["start_ts"], item["end_ts"]))
    timed = [item for item in events if not item["all_day"]]
    conflicts = []
    for i, first in enumerate(timed):
        for second in timed[i + 1:]:
            if second["start_ts"] >= first["end_ts"]:
                break
            if first["start_ts"] < second["end_ts"]:
                conflicts.append({"first": first["title"], "second": second["title"],
                                  "overlap_start": datetime.fromtimestamp(max(first["start_ts"], second["start_ts"]), now.tzinfo).isoformat(),
                                  "overlap_end": datetime.fromtimestamp(min(first["end_ts"], second["end_ts"]), now.tzinfo).isoformat()})
    shown = [{"title": item["title"], "start": datetime.fromtimestamp(item["start_ts"], now.tzinfo).isoformat(),
              "end": datetime.fromtimestamp(item["end_ts"], now.tzinfo).isoformat(),
              "all_day": item["all_day"]} for item in events[:30]]
    return _reply("ok", kind="calendar", period_start=start.isoformat(), period_end=end.isoformat(),
                  event_count=len(events), events=shown, events_truncated=len(events) > len(shown),
                  conflicts=conflicts[:12], conflict_count=len(conflicts),
                  guidance="Yalnızca gerçek zaman örtüşmelerini çakışma say; tüm gün etkinliklerini otomatik çakışma sayma.")


def _folder(path: Path, context: dict) -> str:
    if not path.is_dir() or path.is_symlink():
        return _reply("error", message="Seçili öğe klasör değil.")
    if not _same_app(context):
        return _reply("error", message="Aktif Finder penceresi değişti; klasör okunmadı.")
    extensions = Counter()
    examples = []
    copy_names = []
    loose = folders = scanned = 0
    truncated = False
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if scanned >= MAX_FOLDER_ITEMS:
                    truncated = True
                    break
                scanned += 1
                if entry.is_dir(follow_symlinks=False):
                    folders += 1
                elif entry.is_file(follow_symlinks=False):
                    loose += 1
                    extensions[Path(entry.name).suffix.lower() or "(uzantısız)"] += 1
                if len(examples) < 25:
                    examples.append(entry.name)
                if re.search(r"(?:\s\(\d+\)|\s-\s(?:copy|kopya))\.[^.]+$", entry.name, re.I):
                    copy_names.append(entry.name)
    except OSError:
        return _reply("error", message="Klasör içeriği okunamadı.")
    findings = []
    if loose >= 30 and folders <= 2:
        findings.append(f"Üst düzeyde {loose} dosya ve yalnızca {folders} alt klasör var; konuya göre gruplama yararlı olabilir.")
    if len(extensions) >= 8 and loose >= 15:
        findings.append(f"Üst düzeyde {len(extensions)} farklı dosya türü karışık duruyor.")
    if copy_names:
        findings.append(f"Kopya olabilecek adlar var: {', '.join(copy_names[:5])}. İçerikleri karşılaştırılmadı.")
    return _reply("partial" if truncated else "ok", kind="folder", path=str(path),
                  scanned_top_level=scanned, top_level_files=loose, top_level_folders=folders,
                  extension_counts=dict(extensions.most_common(12)), example_names=examples,
                  findings=findings, truncated=truncated,
                  guidance="Yalnızca üst düzey düzeni değerlendir; aynı adlı dosyaları içerikçe kopya sayma. Hiçbir dosyayı taşıma veya silme.")


FINDER_WINDOW = r'''function run() {
  var windows = Application("Finder").windows();
  if (!windows.length) return JSON.stringify({status:"unavailable"});
  return JSON.stringify({status:"ok",url:String(windows[0].target().url())});
}'''


def _mentions(folded: str, words) -> bool:
    """Kelime başında eşleşir: "ram" artık "program"/"drama" içinde, "web" başka kelimenin ortasında yakalanmaz."""
    return any(re.search(r"(?<!\w)" + re.escape(word), folded) for word in words)


def analyze_current(query: str = "") -> str:
    """Choose one evidence path using explicit subject, foreground app and selection."""
    folded = _fold(query)
    if _mentions(folded, ("takvim", "ajanda", "program", "etkinlik")):
        return _calendar(query)
    if _mentions(folded, ("sistem", "performans", "bilgisayar", "cpu", "ram")):
        return _system()
    context = active_context(include_history=False)
    if context.get("status") in ("error", "permission_required", "unavailable") and not context.get("bundle_id"):
        return _reply("error", message=context.get("message", "Aktif pencere belirlenemedi."))
    app = context.get("bundle_id", "")
    selected = _selected_path(context)
    document = _document_path(context)

    path = selected or document
    if _mentions(folded, ("pdf",)) and (not path or path.suffix.lower() != ".pdf"):
        return _reply("needs_target", message="Analiz edilecek PDF açık veya seçili değil.")
    if _mentions(folded, ("kod", "code", "yazilim")) and (not path or path.suffix.lower() not in CODE_EXTENSIONS):
        return _reply("needs_target", message="Analiz edilecek kod dosyasının kesin yolu belirlenemedi.")
    if _mentions(folded, ("site", "sayfa", "web")) and app not in (
            "com.apple.Safari", "com.google.Chrome", "com.microsoft.edgemac"):
        return _reply("needs_target", message="Analiz edilecek web sayfası açık değil.")
    if _mentions(folded, ("klasor", "dosya duzeni")) and app != "com.apple.finder":
        return _reply("needs_target", message="Analiz edilecek klasör Finder'da açık değil.")
    if path and path.suffix.lower() in CODE_EXTENSIONS:
        return _code(path, context)
    if path and path.suffix.lower() == ".pdf":
        return _pdf(path, context)
    if app in ("com.apple.Safari", "com.google.Chrome", "com.microsoft.edgemac"):
        return _web(context)
    if app == "com.apple.finder":
        if selected and selected.is_dir():
            return _folder(selected, context)
        if context.get("selected_paths") and len(context["selected_paths"]) > 1:
            return _reply("needs_target", message="Birden fazla öğe seçili; hangisini analiz edeyim?")
        folder = _jxa(FINDER_WINDOW)
        current = _file_url_to_path(folder.get("url", ""))
        if folder.get("status") == "ok" and current:
            return _folder(Path(current), context)
        return _reply("needs_target", message="Finder klasörü belirlenemedi; bir klasör seç.")
    if app == "com.apple.Calendar":
        return _calendar(query)
    if app == "com.apple.ActivityMonitor":
        return _system()
    if app == "com.apple.Notes" and len(context.get("selected_notes", [])) == 1:
        note = context["selected_notes"][0]
        return _reply("partial", kind="note", title=note.get("title", ""),
                      text=note.get("preview", ""), message="Seçili notun yalnızca önizlemesi okundu.")
    if not app or context.get("target_source") == "jarvis_only":
        return _reply("needs_target", message="Analiz edilecek pencere veya dosya belirlenemedi.")
    visible = analyze_screen("Aktif pencerede görünen içeriği analiz et; yalnızca gördüğün kanıtları ve belirsizlikleri belirt.",
                             "context_window")
    if _screen_error(visible):
        return _reply("error", message="Aktif ekran okunamadı.", detail=visible)
    return _reply("partial", kind="visible_screen", app=context.get("app", ""),
                  window=context.get("window_title", ""), analysis=visible,
                  message="Yalnızca görünen ekran bölümü incelendi.")
