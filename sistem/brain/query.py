"""İkinci beyin sorguları: JARVIS aracı ve grafik penceresinin ayrıntı paneli.

Kural: Yanıtlar yalnızca indekslenmiş veriden üretilir. Eşleşme yoksa bunu
açıkça söyler; hiçbir zaman indeks dışı dosya tahmin etmez.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from brain import settings, store
from brain.common import REL_TEXT, is_indexing
from brain.textutil import LineIndex, fold, iter_tokens, lower_tr, query_terms, stem, tokens_with_offsets

TYPE_TR = {"note": "not", "file": "dosya", "project": "proje", "topic": "konu"}
ANSWER_POLICY = ("Yalnızca bu yanıttaki indekslenmiş kayıtlara dayan. 'explicit' kayıtlar kaynakta "
                 "yazılı/kesin bağlardır; 'inferred' kayıtlar yerel modelin tahminidir, tahmin olduğunu "
                 "söyle. Listede olmayan not veya dosya uydurma. Taranan yer yalnızca 'sources' "
                 "listesindeki klasörlerdir.")

_cache: dict = {"path": None, "mtime": None, "graph": None}


def _with_adj(graph: dict) -> dict:
    graph["adj"] = {}
    for e in graph["edges"]:
        graph["adj"].setdefault(e["src"], []).append(e)
        graph["adj"].setdefault(e["dst"], []).append(e)
    return graph


def _restrict(graph: dict, sources: list[str]) -> dict:
    """Kullanıcı bir kaynağı kaldırdıysa, yeniden tarama bitene kadar o klasörün verisini gösterme."""
    allowed = {settings.normalize_source(s) for s in sources}  # macOS: /var → /private/var vb.
    indexed = {settings.normalize_source(s) for s in graph["meta"].get("sources", [])}
    if indexed <= allowed:
        return graph
    keep = {nid for nid, n in graph["nodes"].items() if n.get("root") in allowed}
    for e in graph["edges"]:  # konu düğümleri (kökü yok) yalnızca kalan belgelere bağlıysa kalır
        for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
            if a in keep and not graph["nodes"].get(b, {}).get("root"):
                keep.add(b)
    nodes = {nid: n for nid, n in graph["nodes"].items() if nid in keep}
    edges = [e for e in graph["edges"] if e["src"] in keep and e["dst"] in keep]
    return _with_adj({"meta": graph["meta"], "nodes": nodes, "edges": edges})


def load(db_path: Path | None = None, sources: list[str] | None = None) -> dict | None:
    db = Path(db_path or settings.DB_PATH)
    try:
        mtime = db.stat().st_mtime
    except OSError:
        return None
    if _cache["path"] == str(db) and _cache["mtime"] == mtime:
        graph = _cache["graph"]
    else:
        graph = store.load_graph(db)
        if graph is not None:
            _with_adj(graph)
        _cache.update(path=str(db), mtime=mtime, graph=graph)
    if graph is not None and sources is not None:
        return _restrict(graph, sources)
    return graph


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return None


def _evidence_text(ev: list) -> str:
    if not ev:
        return ""
    first = ev[0]
    where = first.get("rel") or first.get("path") or ""
    if first.get("line"):
        where += f":{first['line']}"
    text = first.get("text") or ""
    if not first.get("line") and text.startswith(("Dosya konumu", "Klasör konumu")):
        return f"{where} (klasör konumu)"
    return f"{where} — {text}" if text else where


def _item(node: dict, edge: dict, relation_text: str, reason: str | None = None) -> dict:
    item = {"title": node["label"], "type": TYPE_TR.get(node["type"], node["type"]),
            "path": node.get("path", ""), "relation": relation_text,
            "reason": reason or edge["reason"], "evidence": _evidence_text(edge.get("evidence")),
            "id": node["id"]}
    if edge["kind"] == "inferred":
        item["confidence"] = edge.get("confidence")
    return item


def _other(edge: dict, nid: str) -> str:
    return edge["dst"] if edge["src"] == nid else edge["src"]


def describe_relations(graph: dict, nid: str, limit: int = 60) -> tuple[list, list]:
    """Bir düğümün açık ve tahmini ilişkileri (yön bilgisiyle)."""
    nodes = graph["nodes"]
    explicit, inferred = [], []
    for e in graph["adj"].get(nid, []):
        other = nodes.get(_other(e, nid))
        if other is None:
            continue
        rel = REL_TEXT.get(e["relation"], e["relation"])
        if e["relation"] in ("belongs_to", "part_of") and e["dst"] == nid:
            rel = "bu klasörde" if e["relation"] == "belongs_to" else "alt klasör"
        elif e["relation"] == "links_to" and e["dst"] == nid:
            rel = "buraya bağlantı veriyor"
        elif e["relation"] == "mentions" and e["dst"] == nid:
            rel = "bunun adını anıyor"
        elif e["relation"] == "tagged" and e["dst"] == nid:
            rel = "bu etikete sahip"
        elif e["relation"] == "about_topic" and e["dst"] == nid:
            rel = "bu konuda (tahmin)"
        (inferred if e["kind"] == "inferred" else explicit).append(_item(other, e, rel))
    order = {"not": 0, "dosya": 1, "proje": 2, "konu": 3}
    explicit.sort(key=lambda i: (order.get(i["type"], 9), i["title"].lower()))
    inferred.sort(key=lambda i: -(i.get("confidence") or 0))
    return explicit[:limit], inferred[:limit]


def _project_members(graph: dict, pid: str, depth: int = 3) -> tuple[set, set]:
    """Proje klasöründeki dosyalar (alt proje klasörleri dahil) ve alt projeler."""
    projects = {pid}
    frontier = {pid}
    for _ in range(depth):
        nxt = set()
        for p in frontier:
            for e in graph["adj"].get(p, []):
                if e["relation"] == "part_of" and e["dst"] == p:
                    nxt.add(e["src"])
        nxt -= projects
        projects |= nxt
        frontier = nxt
    members = set()
    for p in projects:
        for e in graph["adj"].get(p, []):
            if e["relation"] == "belongs_to" and e["dst"] == p:
                members.add(e["src"])
    return members, projects


def project_view(graph: dict, pid: str, limit: int = 12) -> tuple[list, list]:
    nodes = graph["nodes"]
    members, projects = _project_members(graph, pid)
    explicit, inferred, seen = [], [], set()
    for mid in members:
        for e in graph["adj"].get(mid, []):
            if e["relation"] == "belongs_to" and e["src"] == mid and e["dst"] in projects:
                node = nodes[mid]
                where = "proje klasöründe" if e["dst"] == pid else f"alt klasörde ({nodes[e['dst']]['label']})"
                explicit.append(_item(node, e, where))
                seen.add(mid)
                break
    for e in graph["adj"].get(pid, []):
        if e["relation"] in ("mentions", "links_to") and e["dst"] == pid and e["src"] not in seen:
            explicit.append(_item(nodes[e["src"]], e, "proje adını anıyor"))
            seen.add(e["src"])
    for mid in members:
        for e in graph["adj"].get(mid, []):
            other = _other(e, mid)
            if other in members or other in seen:
                continue
            if e["kind"] == "explicit" and e["relation"] in ("links_to", "mentions"):
                explicit.append(_item(nodes[other], e, "proje dosyasıyla bağlantılı",
                                      f"{e['reason']} (proje dosyası: {nodes[mid]['label']})"))
                seen.add(other)
    best: dict[str, tuple] = {}
    for mid in members:
        for e in graph["adj"].get(mid, []):
            if e["kind"] != "inferred" or e["relation"] != "similar_content":
                continue
            other = _other(e, mid)
            if other in members or other in seen:
                continue
            if other not in best or (e.get("confidence") or 0) > (best[other][0].get("confidence") or 0):
                best[other] = (e, mid)
    for other, (e, mid) in best.items():
        inferred.append(_item(nodes[other], e, "projeyle benzer içerik",
                              f"Projedeki ‘{nodes[mid]['label']}’ ile: {e['reason']}"))
    order = {"not": 0, "dosya": 1, "proje": 2, "konu": 3}
    explicit.sort(key=lambda i: (order.get(i["type"], 9), i["title"].lower()))
    inferred.sort(key=lambda i: -(i.get("confidence") or 0))
    return explicit[:limit], inferred[:limit]


def match_nodes(graph: dict, query: str, limit: int = 8) -> list[tuple[float, dict]]:
    terms = query_terms(query)
    whole = fold(query).strip()
    if not terms and not whole:
        return []
    scored = []
    for node in graph["nodes"].values():
        label = node["label"]
        if node["type"] in ("note", "file"):
            label = Path(label).stem
        lf = fold(label).lstrip("#")
        pf = fold(node.get("rel", ""))
        score = 0.0
        if whole and lf == whole.lstrip("#"):
            score = 20.0
        elif terms and all(t in lf for t in terms):
            score = 12.0 + (2.0 if lf.startswith(terms[0]) else 0.0)
        elif terms and all(t in pf for t in terms):
            score = 7.0
        elif terms:
            hit = sum(1 for t in terms if t in lf)
            score = 5.0 * hit / len(terms)
        if score <= 0:
            continue
        score += {"project": 3.0, "topic": 2.0, "note": 1.0}.get(node["type"], 0.0)
        score += min(2.0, node.get("degree", 0) / 10.0)
        scored.append((score, node))
    scored.sort(key=lambda s: -s[0])
    return scored[:limit]


def content_matches(graph: dict, query: str, db_path: Path, limit: int = 6, exclude=()) -> list[dict]:
    terms = query_terms(query)
    if not terms:
        return []
    node_by_path = {n["path"]: n for n in graph["nodes"].values() if n["type"] in ("note", "file")}
    out = []
    for path, text in store.search_texts(db_path, terms, limit=limit * 3):
        node = node_by_path.get(path)
        if node is None or node["id"] in exclude:
            continue
        # fold() uzunluğu değiştirebilir ("…"→"...", "ﬁ"→"fi"); konumu satır satır bul.
        line_no, line = 1, ""
        for number, raw in enumerate(text.splitlines(), 1):
            if terms[0] in fold(raw):
                line_no, line = number, " ".join(raw.split())[:160]
                break
        out.append({"title": node["label"], "type": TYPE_TR.get(node["type"]), "path": path,
                    "relation": "metinde geçiyor", "reason": "Sorgu terimleri dosya metninde geçiyor",
                    "evidence": f"{node.get('rel')}:{line_no} — {line}", "id": node["id"]})
        if len(out) >= limit:
            break
    return out


READ_POLICY = ("Soruyu YALNIZCA bu 'excerpts' alıntılarına dayanarak yanıtla ve her bilgiyi hangi dosyadan "
               "aldığını dosya adıyla söyle. Alıntılarda cevap yoksa ya da found=0 ise 'notlarında bu bilgi "
               "yok' de; genel bilgiyle tamamlama, tahmin ekleme. Alıntılar kısaltılmış olabilir; eksikse "
               "bunu belirt. Birden fazla not çelişirse ikisini de dosya adıyla söyle.")

# Soru cümlelerindeki, not içeriğiyle ilgisi olmayan sözcükler (kök olarak karşılaştırılır).
_QUESTION_WORDS = """özetle özet anlat söyle söyler yazdım yazdığım yazmıştım yazmış yazdıklarım bahset bahsettim
bahsetmiştim notlarım notlarımda notlarımdan notumda notum neler nelerdir hangi hangileri
ver verir misin mısın musun neydi nedir nelerdi kadardı nasıldı demiştim demiştik dedim dedik miydi mıydı lütfen jarvis ikinci beyin beynim beynimde beynimdeki grafik grafikte oku okur
bul bak kaydettiğim kaydetmiştim hatırlat hatırla içinde içeriği içerik ile alakalı konusunda konulu
proje projesinde projede projemde projenin projeyi projedeki
"""
_QUESTION_STEMS = frozenset(stem(fold(w)) for w in _QUESTION_WORDS.split())
_HEADING = ("#", "==", "--")


def _read_stems(question: str) -> list[str]:
    out = []
    for _, folded, st in iter_tokens(question):
        if st in _QUESTION_STEMS or folded in _QUESTION_STEMS:
            continue
        if st not in out:
            out.append(st)
    return out[:10]


def _chunks(text: str, target: int = 650, hard: int = 900) -> list[tuple[int, str]]:
    """Metni paragraf sınırlarından ~target karakterlik parçalara böler: [(başlangıç konumu, parça)]."""
    parts, pos = [], 0
    for block in text.split("\n\n"):
        start = text.find(block, pos) if block else pos
        pos = start + len(block)
        if not block.strip():
            continue
        if len(block) <= hard:
            parts.append((start, block))
            continue
        cur, cur_start = "", start
        off = start
        for line in block.split("\n"):
            if cur and len(cur) + len(line) + 1 > target:
                parts.append((cur_start, cur))
                cur, cur_start = "", off
            cur = f"{cur}\n{line}" if cur else line
            off += len(line) + 1
        if cur.strip():
            parts.append((cur_start, cur))
    merged: list[tuple[int, str]] = []
    for start, chunk in parts:
        if merged and len(merged[-1][1]) < 220 and len(merged[-1][1]) + len(chunk) < target:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n\n" + chunk)
        else:
            merged.append((start, chunk))
    return merged


def _project_of(graph: dict, nid: str) -> str:
    for e in graph["adj"].get(nid, []):
        if e["relation"] == "belongs_to" and e["src"] == nid:
            return graph["nodes"].get(e["dst"], {}).get("label", "")
    return ""


def read_notes(graph: dict, question: str, db_path: Path, max_excerpts: int = 6,
               max_chars: int = 6000) -> dict:
    """Soru için ilgili notlardan kısa alıntılar (dosya adı ve satır aralığıyla).

    Yalnızca indekslenmiş (seçili klasörlerdeki) dosyaların metni okunur. Alıntılar JARVIS'in sesli
    asistan modeline cevap üretmesi için gönderilir; bu yüzden sayı ve uzunluk sınırlıdır.
    """
    stems = _read_stems(question)
    nodes = graph["nodes"]
    doc_nodes = {n["path"]: n for n in nodes.values() if n["type"] in ("note", "file") and n.get("path")}
    boost: dict[str, float] = {}
    focus = None
    # Soru içinde adı geçen proje/konu/not (en uzun ad kazanır): "Güneş Paneli projesinde bütçe…"
    fq = " " + " ".join(fold(question).replace("#", " ").split()) + " "
    best_len = 0
    for n in nodes.values():
        name = Path(n["label"]).stem if n["type"] in ("note", "file") else n["label"]
        key = " ".join(fold(name).lstrip("#").replace("_", " ").replace("-", " ").split())
        if len(key) >= 4 and f" {key}" in fq and len(key) > best_len:
            focus, best_len = n, len(key)
    matches = match_nodes(graph, question, limit=5) if focus is None else []
    if matches and matches[0][0] >= 8.0:
        focus = matches[0][1]
    if focus is not None:
        if focus["type"] in ("note", "file"):
            boost[focus["path"]] = 6.0
        elif focus["type"] == "project":
            members, _ = _project_members(graph, focus["id"])
            for mid in members:
                if nodes[mid].get("path"):
                    boost[nodes[mid]["path"]] = 3.0
        for e in graph["adj"].get(focus["id"], []):
            other = nodes.get(_other(e, focus["id"]), {})
            if other.get("path") and e["kind"] == "explicit":
                boost.setdefault(other["path"], 2.0)
    texts: dict[str, str] = {}
    doc_hits: dict[str, int] = {}
    for path, text, hits in store.search_texts_ranked(db_path, stems, limit=15):
        if path in doc_nodes:
            texts[path] = text
            doc_hits[path] = hits
    missing = [p for p in boost if p not in texts and p in doc_nodes]
    texts.update({p: t for p, t in store.texts_for_paths(db_path, missing[:20]).items() if p in doc_nodes})
    scored = []
    for path, text in texts.items():
        if not text.strip():
            continue
        li = None
        for start, chunk in _chunks(text):
            low = lower_tr(chunk)
            chunk_stems = [st for _, _, _, st in tokens_with_offsets(low)]
            present = {st for st in stems if st in chunk_stems}
            count = sum(1 for st in chunk_stems if st in stems)
            score = 3.0 * len(present) + min(count, 8) * 0.5 + boost.get(path, 0.0)
            if chunk.lstrip().startswith(_HEADING) and present:
                score += 1.0
            if not present and not boost.get(path):
                continue
            li = li or LineIndex(text)
            first = li.line_of(start)
            last = li.line_of(start + len(chunk.rstrip()) - 1)
            scored.append((score, len(present), path, start, first, last, chunk))
    scored.sort(key=lambda x: (-x[0], -x[1], x[4]))
    top_terms = max((x[1] for x in scored), default=0)
    need = max(1, (top_terms + 1) // 2)   # en iyi parçanın yarısı kadar terim içermeyenler gürültüdür
    excerpts, per_doc, used = [], {}, 0
    for score, n_present, path, start, first, last, chunk in scored:
        if n_present < need and not boost.get(path):
            continue
        if len(excerpts) >= max_excerpts or used >= max_chars:
            break
        if per_doc.get(path, 0) >= 2:
            continue
        if n_present == 0 and per_doc.get(path, 0) >= 1:
            continue   # yalnızca odak sayesinde gelen dosyadan en fazla bir (ilk) parça
        text = chunk.strip()
        if len(text) > 900:
            text = text[:899].rstrip() + "…"
        text = text[: max(0, max_chars - used)]
        node = doc_nodes[path]
        excerpts.append({"title": node["label"], "type": TYPE_TR.get(node["type"]), "path": path,
                         "rel": node.get("rel", ""), "project": _project_of(graph, node["id"]) or None,
                         "lines": f"{first}-{last}" if last > first else str(first),
                         "matched_terms": n_present, "id": node["id"], "text": text})
        per_doc[path] = per_doc.get(path, 0) + 1
        used += len(text)
    return {"question": question, "terms": stems,
            "focus": ({"title": focus["label"], "type": TYPE_TR.get(focus["type"]), "id": focus["id"]}
                      if focus else None),
            "found": len(excerpts), "excerpts": excerpts}


def status(db_path: Path | None = None) -> dict:
    db = Path(db_path or settings.DB_PATH)
    sources = settings.get_sources()
    meta = store.load_meta(db)
    return {
        "status": "ok" if sources else "needs_setup",
        "sources": sources,
        "indexed": bool(meta),
        "indexing": is_indexing(db.with_suffix(".lock")),
        "built_at": _iso(meta.get("built_at")),
        "nodes": meta.get("nodes", 0), "edges": meta.get("edges", 0),
        "explicit_edges": meta.get("explicit_edges", 0), "inferred_edges": meta.get("inferred_edges", 0),
        "by_type": meta.get("by_type", {}), "truncated": meta.get("truncated", False),
        "stale_sources": bool(meta) and sorted(meta.get("sources", [])) != sorted(sources),
        "hand_control_enabled": settings.hand_control_enabled(),
        "message": "" if sources else ("Kaynak klasör seçilmemiş. Ayarlar › İKİNCİ BEYİN · KAYNAKLAR "
                                       "bölümünden not/proje klasörü ekle."),
    }


def second_brain(action: str = "query", query: str = "", limit: int = 12,
                 db_path: Path | None = None) -> str:
    action = (action or "query").strip().lower()
    db = Path(db_path or settings.DB_PATH)
    info = status(db)
    if action == "status":
        return json.dumps(info, ensure_ascii=False)
    if not info["sources"]:
        return json.dumps({"status": "needs_setup", "message": info["message"],
                           "answer_policy": ANSWER_POLICY}, ensure_ascii=False)
    graph = load(db, info["sources"])
    if graph is None:
        return json.dumps({"status": "not_indexed", "sources": info["sources"], "indexing": info["indexing"],
                           "message": "Seçili klasörler henüz indekslenmedi; tarama bitince tekrar sorulabilir.",
                           "answer_policy": ANSWER_POLICY}, ensure_ascii=False)
    limit = max(1, min(int(limit or 12), 30))
    base = {"status": "ok", "query": query, "sources": info["sources"], "indexed_at": info["built_at"],
            "stale_sources": info["stale_sources"], "answer_policy": ANSWER_POLICY}
    if action == "read":
        if not _read_stems(query):
            return json.dumps({**base, "status": "needs_query", "answer_policy": READ_POLICY,
                               "message": "Soru boş ya da yalnızca genel sözcüklerden oluşuyor; konu/proje adıyla sor."},
                              ensure_ascii=False)
        found = read_notes(graph, query, db)
        result = {**base, **found, "answer_policy": READ_POLICY}
        if not found["found"]:
            result["message"] = (f"Seçili klasörlerdeki notlarda ‘{query}’ ile ilgili bir şey bulunamadı. "
                                 "Bu, yalnızca indekslenen klasörler için geçerlidir.")
        return json.dumps(result, ensure_ascii=False)
    terms = query_terms(query)
    if not terms:
        if action == "show":
            return json.dumps({**base, "focus": None, "message": "Genel grafik açıldı.",
                               "nodes": info["nodes"], "edges": info["edges"]}, ensure_ascii=False)
        return json.dumps({**base, "status": "needs_query",
                           "message": ("Sorguda proje/konu adı yok. 'Bu proje' gibi belirsiz ifadede önce "
                                       "get_active_context ile aktif klasör/proje adını doğrula, sonra adla sor.")},
                          ensure_ascii=False)
    matches = match_nodes(graph, query)
    focus = matches[0][1] if matches and matches[0][0] >= 8.0 else None
    if focus is not None:
        if focus["type"] == "project":
            explicit, inferred = project_view(graph, focus["id"], limit)
        else:
            explicit, inferred = describe_relations(graph, focus["id"], limit)
    else:
        explicit, inferred = [], []
    listed = {i["id"] for i in explicit + inferred} | ({focus["id"]} if focus else set())
    text_hits = content_matches(graph, query, db, limit=6, exclude=listed)
    others = [{"title": n["label"], "type": TYPE_TR.get(n["type"]), "path": n.get("path", ""), "id": n["id"]}
              for s, n in matches[1:6] if s >= 5.0 and n["id"] not in listed]
    found = len(explicit) + len(inferred) + len(text_hits) + (1 if focus else 0)
    result = {**base, "found": found,
              "focus": ({"title": focus["label"], "type": TYPE_TR.get(focus["type"]),
                         "path": focus.get("path", ""), "id": focus["id"]} if focus else None),
              "explicit": explicit, "inferred": inferred, "content_matches": text_hits,
              "other_matches": others}
    if not found:
        result["message"] = (f"İndekslenen klasörlerde ‘{query}’ ile eşleşen not/dosya yok. "
                             "Bu, yalnızca seçili kaynak klasörler için geçerlidir.")
    return json.dumps(result, ensure_ascii=False)
