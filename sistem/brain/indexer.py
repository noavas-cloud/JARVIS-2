"""İkinci beyin indeksleyicisi.

Yalnızca kullanıcının ayarlardan seçtiği kaynak klasörleri tarar. Bağlantılar
iki sınıftadır:

  * explicit (açık):   dosyada/klasörde gerçekten var olan bağ — [[wiki]] ya da
                        Markdown bağlantısı, #etiket, dosya/proje adının geçmesi,
                        dosyanın proje klasöründe durması. Kanıt: yol + satır.
  * inferred (tahmini): yerel istatistiksel modelin (TF‑IDF benzerliği ve anahtar
                        terim çıkarımı) önerdiği ilişki. Güven puanı ve gerekçe taşır.
                        Metinler buluta gönderilmez.

Komut satırı:  python -m brain.indexer --build     (ayarlardaki kaynaklarla)
               python -m brain.indexer --build --source /yol/klasor --db /tmp/x.sqlite3
Çıktı: satır başına bir JSON ilerleme olayı (stdout).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain import extract as ex  # noqa: E402
from brain import store  # noqa: E402
from brain.common import REL_TEXT, IndexLock, is_indexing  # noqa: E402,F401
from brain.layout import compute_layout  # noqa: E402
from brain.textutil import LineIndex, fold, lower_tr, stem, tokens_with_offsets  # noqa: E402

SKIP_DIRS = {"node_modules", "venv", ".venv", "env", "__pycache__", "Library", "build", "dist",
             "DerivedData", "Pods", "site-packages", "hand_env", "bower_components", "target",
             ".git", ".svn", ".hg", ".idea", ".vscode", ".Trash", "Caches"}
SKIP_DIR_PREFIXES = ("_YEDEK_",)   # tüm JARVIS yedek klasörleri (grafiği kalabalıklaştırıyordu)
PACKAGE_SUFFIXES = (".app", ".bundle", ".framework", ".photoslibrary", ".xcodeproj", ".xcworkspace",
                    ".pkg", ".plugin", ".kext", ".sparsebundle", ".musiclibrary", ".fcpbundle",
                    ".imovielibrary", ".tvlibrary", ".xcassets", ".lproj", ".rtfd", ".pages",
                    ".numbers", ".key", ".swiftpm", ".playground")
IGNORED_EXTS = {".pyc", ".pyo", ".o", ".a", ".so", ".dylib", ".class", ".lock", ".log", ".tmp",
                ".swp", ".ds_store", ".part", ".crdownload", ".sqlite3-journal", ".pcm",
                ".swiftmodule", ".swiftdoc"}
PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "setup.py", "requirements.txt",
                   "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
                   "CMakeLists.txt", "Makefile", "Gemfile", "composer.json", "Package.swift",
                   ".obsidian", "README.md", "README", "readme.md", "README.txt", "BENIOKU.md")
GENERIC_NAMES = {fold(x) for x in """src source docs doc notes notlar not test tests lib libs assets
    images img image data archive arsiv arşiv misc temp tmp build dist public private resources
    scripts backup yedek belgeler documents downloads desktop projects projeler klasor klasör
    yeni eski old new general genel draft taslak untitled adsiz adsız inbox gelen files dosyalar
    media medya config ayarlar other diger diğer output outputs input inputs app apps main""".split()}

WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]{1,200})(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")
MDLINK_RE = re.compile(r"(!?)\[([^\]\n]{0,200})\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")
TAG_RE = re.compile(r"(?:(?<=\s)|(?<=^))#([^\s#.,;:!?()\[\]{}\"'`<>|/\\]{2,40})", re.M)
FILENAME_RE = re.compile(r"[a-z0-9][a-z0-9_\-. ]{0,80}?\.[a-z0-9]{2,5}(?![a-z0-9])")
HEX_RE = re.compile(r"^[0-9a-fA-F]{3,8}$")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)

SIM_THRESHOLD = 0.20
SIM_PER_NODE = 4
TOPIC_TOP_TERMS = 8
MAX_MENTIONS_PER_DOC = 25



@dataclass
class Entry:
    path: str
    root: str
    rel: str
    ext: str
    kind: str
    size: int
    mtime: float


@dataclass
class Scan:
    entries: list = field(default_factory=list)
    project_dirs: dict = field(default_factory=dict)  # dir -> neden
    roots: list = field(default_factory=list)
    missing_roots: list = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    truncated: bool = False


def _emit(progress, **event):
    if progress:
        progress(event)


def scan_sources(sources: list[str], max_files: int = 4000, deadline: float | None = None,
                 opened: list | None = None) -> Scan:
    """Yalnızca verilen köklerin altını dolaşır; sembolik bağlantıları izlemez."""
    result = Scan()
    for raw_root in sources:
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            result.missing_roots.append(str(root))
            continue
        result.roots.append(str(root))
        if opened is not None:
            opened.append(str(root))

        def onerror(_exc):
            result.skipped["erişilemeyen klasör"] += 1

        for cur, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
            curp = Path(cur)
            names = set(dirs) | set(files)
            marker = next((m for m in PROJECT_MARKERS if m in names), None)
            if marker is None:
                marker = next((d for d in dirs if d.endswith((".xcodeproj", ".xcworkspace"))), None)
            if marker:
                result.project_dirs[str(curp)] = f"Proje işareti bulundu: {marker}"
            kept = []
            for d in sorted(dirs):
                full = os.path.join(cur, d)
                if (d.startswith(".") or d in SKIP_DIRS or d.startswith(SKIP_DIR_PREFIXES)
                        or d.lower().endswith(PACKAGE_SUFFIXES) or os.path.islink(full)):
                    result.skipped["atlanan klasör"] += 1
                    continue
                kept.append(d)
            dirs[:] = kept
            for name in sorted(files):
                if name.startswith((".", "~$")):
                    continue
                full = curp / name
                ext = full.suffix.lower()
                if ext in IGNORED_EXTS or name.lower() == ".ds_store":
                    continue
                try:
                    if full.is_symlink():
                        result.skipped["sembolik bağlantı"] += 1
                        continue
                    st = full.stat()
                except OSError:
                    result.skipped["okunamayan dosya"] += 1
                    continue
                result.entries.append(Entry(str(full), str(root), str(full.relative_to(root)), ext,
                                            ex.classify(full), st.st_size, st.st_mtime))
                if len(result.entries) >= max_files or (deadline and time.monotonic() > deadline):
                    result.truncated = True
                    return result
    return result


def _frontmatter_tags(text: str) -> list[tuple[str, int]]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1 or end > 4000:
        return []
    block = text[3:end]
    tags: list[tuple[str, int]] = []
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*(tags|tag|etiketler|etiket|keywords)\s*:\s*(.*)$", line, re.I)
        if m:
            value = m.group(2).strip()
            line_no = i + 1  # lines[0] '---' satırının kalanıdır; lines[i] dosyada i+1. satır
            if value:
                for t in re.split(r"[,\s]+", value.strip("[]")):
                    t = t.strip().strip("'\"#")
                    if t:
                        tags.append((t, line_no))
            else:
                j = i + 1
                while j < len(lines) and re.match(r"\s*-\s+", lines[j]):
                    t = re.sub(r"^\s*-\s+", "", lines[j]).strip().strip("'\"#")
                    if t:
                        tags.append((t, j + 1))
                    j += 1
                i = j - 1
        i += 1
    return tags


def _inline_tags(text: str) -> list[tuple[str, int]]:
    # Kod blokları içindeki #include vb. etiket sayılmaz.
    masked = FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    lines = LineIndex(masked)
    out = []
    for m in TAG_RE.finditer(masked):
        tag = m.group(1).rstrip("-_")
        if len(tag) < 2 or tag.isdigit() or (HEX_RE.match(tag) and any(c.isdigit() for c in tag)):
            continue
        out.append((tag, lines.line_of(m.start())))
    return out


def _ev(entry: Entry, line_no: int, text: str) -> dict:
    return {"path": entry.path, "rel": entry.rel, "line": line_no, "text": text}


class GraphBuilder:
    def __init__(self, scan: Scan, texts: dict[str, dict]):
        self.scan = scan
        self.texts = texts
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.entry_by_path = {e.path: e for e in scan.entries}
        self.node_of_path: dict[str, str] = {}

    # ── düğümler ──────────────────────────────────────────────────────────
    def add_node(self, nid, ntype, label, **kw):
        if nid not in self.nodes:
            node = {"id": nid, "type": ntype, "label": label, "meta": {}, **kw}
            self.nodes[nid] = node
        return self.nodes[nid]

    def add_edge(self, src, dst, kind, relation, reason, evidence=None, confidence=None):
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return None
        key = (src, dst, relation)
        edge = self.edges.get(key)
        if edge is None:
            edge = {"src": src, "dst": dst, "kind": kind, "relation": relation, "reason": reason,
                    "evidence": [], "confidence": confidence, "count": 0}
            self.edges[key] = edge
        edge["count"] += 1
        if evidence and len(edge["evidence"]) < 3:
            edge["evidence"].append(evidence)
        if confidence is not None and (edge["confidence"] or 0) < confidence:
            edge["confidence"] = confidence
        return edge

    def build_projects(self):
        roots = self.scan.roots
        proj_dirs: dict[str, str] = {}
        for root in roots:
            proj_dirs[root] = "Seçtiğin kaynak klasör"
        used_dirs = set()
        for e in self.scan.entries:
            rel_parts = Path(e.rel).parts
            if len(rel_parts) > 1:
                used_dirs.add(str(Path(e.root) / rel_parts[0]))
            for parent in Path(e.path).parents:
                if str(parent) == e.root:
                    break
                if str(parent) in self.scan.project_dirs:
                    used_dirs.add(str(parent))
        for d in sorted(used_dirs):
            if d in self.scan.project_dirs:
                proj_dirs[d] = self.scan.project_dirs[d]
            elif str(Path(d).parent) in roots:
                proj_dirs[d] = "Kaynak klasörün doğrudan alt klasörü"
        for d, reason in self.scan.project_dirs.items():
            if d in roots:
                proj_dirs[d] = "Seçtiğin kaynak klasör · " + reason
        self.project_dirs = proj_dirs
        for d, reason in proj_dirs.items():
            root = next((r for r in roots if d == r or Path(d).is_relative_to(Path(r))), "")
            rel = "" if d == root else str(Path(d).relative_to(root))
            self.add_node("project:" + d, "project", Path(d).name or d, path=d, rel=rel, root=root,
                          meta={"project_reason": reason, "is_source_root": d == root})
        for d in proj_dirs:
            if d in roots:
                continue
            parent = self._nearest_project(Path(d).parent)
            if parent:
                self.add_edge("project:" + d, "project:" + parent, "explicit", "part_of",
                              f"‘{Path(d).name}’ klasörü ‘{Path(parent).name}’ klasörünün içinde",
                              {"path": d, "rel": self.nodes["project:" + d]["rel"], "line": None,
                               "text": "Klasör konumu"})

    def _nearest_project(self, start: Path) -> str | None:
        for p in (start, *start.parents):
            if str(p) in self.project_dirs:
                return str(p)
        return None

    def build_files(self):
        for e in self.scan.entries:
            nid = ("note:" if e.kind == "note" else "file:") + e.path
            text = self.texts.get(e.path, {}).get("text", "")
            meta = {"ext": e.ext, "text_chars": len(text),
                    "extract": self.texts.get(e.path, {}).get("method", "")}
            self.add_node(nid, e.kind, Path(e.path).name, path=e.path, rel=e.rel, root=e.root,
                          size=e.size, mtime=e.mtime, meta=meta)
            self.node_of_path[e.path] = nid
            self.node_of_path.setdefault(unicodedata.normalize("NFC", e.path), nid)
            proj = self._nearest_project(Path(e.path).parent)
            if proj:
                self.add_edge(nid, "project:" + proj, "explicit", "belongs_to",
                              f"Dosya ‘{Path(proj).name}’ klasöründe duruyor",
                              {"path": e.path, "rel": e.rel, "line": None, "text": "Dosya konumu: " + e.rel})

    # ── açık bağlantılar ──────────────────────────────────────────────────
    def _resolve_wikilink(self, target: str, source: Entry) -> tuple[str | None, str]:
        key = fold(target.strip()).removesuffix(".md")
        cands = self._title_index.get(key) or self._relpath_index.get(key) or []
        if not cands:
            tail = key.split("/")[-1]
            cands = self._title_index.get(tail, []) if "/" in key else []
        if not cands:
            return None, ""
        if len(cands) == 1:
            return cands[0], ""
        same_dir = [c for c in cands if Path(self.nodes[c]["path"]).parent == Path(source.path).parent]
        if same_dir:
            return same_dir[0], f" (aynı adlı {len(cands)} not var; aynı klasördeki seçildi)"
        same_root = [c for c in cands if self.nodes[c]["root"] == source.root]
        pick = (same_root or cands)[0]
        return pick, f" (aynı adlı {len(cands)} not var; ilki seçildi)"

    def build_explicit_links(self):
        self._title_index: dict[str, list[str]] = defaultdict(list)
        self._relpath_index: dict[str, list[str]] = defaultdict(list)
        filename_index: dict[str, list[str]] = defaultdict(list)
        for e in self.scan.entries:
            nid = self.node_of_path[e.path]
            p = Path(e.rel)
            self._title_index[fold(p.stem)].append(nid)
            self._title_index[fold(p.name)].append(nid)
            self._relpath_index[fold(str(p.with_suffix("")))].append(nid)
            if len(p.name) >= 5 and "." in p.name:
                filename_index[fold(p.name)].append(nid)
        project_names = {}
        for d in self.project_dirs:
            name = fold(Path(d).name)
            if len(name) >= 4 and name not in GENERIC_NAMES and not name.isdigit():
                project_names.setdefault(name, []).append("project:" + d)
        project_re = None
        if project_names:
            alts = sorted(project_names, key=len, reverse=True)
            project_re = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(a) for a in alts) + r")(?![a-z0-9])")

        for e in self.scan.entries:
            text = self.texts.get(e.path, {}).get("text", "")
            if not text:
                continue
            src = self.node_of_path[e.path]
            lines = LineIndex(text)
            is_note = e.kind == "note" or e.ext in (".md", ".markdown")
            if is_note:
                for m in WIKILINK_RE.finditer(text):
                    target, note = self._resolve_wikilink(m.group(1), e)
                    line_no, line = lines.evidence(m.start())
                    if target:
                        self.add_edge(src, target, "explicit", "links_to",
                                      f"[[{m.group(1).strip()}]] wiki bağlantısı{note}", _ev(e, line_no, line))
                    else:
                        unresolved = self.nodes[src]["meta"].setdefault("unresolved_links", [])
                        if len(unresolved) < 20 and m.group(1).strip() not in unresolved:
                            unresolved.append(m.group(1).strip())
                for m in MDLINK_RE.finditer(text):
                    href = m.group(3)
                    if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("#"):
                        continue
                    rel_target = unquote(href.split("#", 1)[0].split("?", 1)[0])
                    if not rel_target:
                        continue
                    try:
                        cand = (Path(e.path).parent / rel_target).resolve()
                    except (ValueError, OSError, RuntimeError):
                        continue  # ör. "%00" içeren bağlantı: tüm dizinlemeyi durdurmasın
                    # macOS dosya adları NFD, not metni NFC olabilir: iki biçimi de dene
                    target = (self.node_of_path.get(str(cand))
                              or self.node_of_path.get(unicodedata.normalize("NFC", str(cand))))
                    line_no, line = lines.evidence(m.start())
                    if target:
                        kind_txt = "Markdown görseli" if m.group(1) else "Markdown bağlantısı"
                        self.add_edge(src, target, "explicit", "links_to",
                                      f"{kind_txt}: {href}", _ev(e, line_no, line))
                tag_hits = _frontmatter_tags(text) + _inline_tags(text)
                for tag, line_no in tag_hits:
                    key = fold(tag)
                    tid = "topic:" + key
                    node = self.add_node(tid, "topic", "#" + tag.lstrip("#"),
                                         meta={"origin": "tag"})
                    node["meta"].setdefault("origin", "tag")
                    self.add_edge(src, tid, "explicit", "tagged", f"Notta #{tag} etiketi var",
                                  _ev(e, line_no, lines.line_text(line_no)))
            folded = self.texts.get(e.path, {}).get("folded") or fold(text)
            flines = LineIndex(folded)
            mentions = 0
            for m in FILENAME_RE.finditer(folded):
                # Eşleşme cümlenin başından başlayabilir: "bu hafta rapor.pdf" → en uzun
                # bilinen dosya adını sondan geriye doğru dene ("hafta rapor.pdf", "rapor.pdf").
                words = m.group(0).strip(" .").split(" ")
                targets = None
                for k in range(min(len(words), 6), 0, -1):
                    targets = filename_index.get(" ".join(words[-k:]))
                    if targets:
                        break
                if not targets:
                    continue
                for target in targets[:2]:
                    if target == src:
                        continue
                    line_no = flines.line_of(m.start())
                    self.add_edge(src, target, "explicit", "mentions",
                                  f"Metinde dosya adı geçiyor: {Path(self.nodes[target]['path']).name}",
                                  _ev(e, line_no, lines.line_text(line_no)))
                    mentions += 1
                if mentions >= MAX_MENTIONS_PER_DOC:
                    break
            if project_re is not None:
                seen = set()
                for m in project_re.finditer(folded):
                    for pid in project_names.get(m.group(1), []):
                        if pid in seen:
                            continue
                        seen.add(pid)
                        own = self._nearest_project(Path(e.path).parent)
                        if own and pid == "project:" + own:
                            continue
                        line_no = flines.line_of(m.start())
                        self.add_edge(src, pid, "explicit", "mentions",
                                      f"Metinde proje adı geçiyor: {self.nodes[pid]['label']}",
                                      _ev(e, line_no, lines.line_text(line_no)))

    # ── tahmini ilişkiler (yerel model) ──────────────────────────────────
    def build_inferred(self):
        docs = []
        for e in self.scan.entries:
            t = self.texts.get(e.path, {}).get("text", "")
            if len(t.strip()) >= 60:
                docs.append(e)
        n = len(docs)
        if n < 2:
            return
        tf: dict[str, Counter] = {}
        first_pos: dict[str, dict] = {}
        surface = defaultdict(Counter)
        df = Counter()
        for e in docs:
            text = self.texts[e.path]["text"]
            counts = Counter()
            firsts = {}
            for offset, s_surface, _s_folded, s_stem in tokens_with_offsets(lower_tr(text)):
                counts[s_stem] += 1
                surface[s_stem][s_surface] += 1
                if s_stem not in firsts:
                    firsts[s_stem] = offset
            if not counts:
                continue
            tf[e.path] = counts
            first_pos[e.path] = firsts
            df.update(counts.keys())
        docs = [e for e in docs if e.path in tf]
        n = len(docs)
        if n < 2:
            return
        max_df = n if n < 10 else int(0.6 * n) + 1
        vectors: dict[str, dict[str, float]] = {}
        for e in docs:
            weights = {}
            for s, c in tf[e.path].items():
                if df[s] > max_df or len(s) < 3:
                    continue
                weights[s] = (1.0 + math.log(c)) * math.log(1.0 + n / df[s])
            top = dict(sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:40])
            norm = math.sqrt(sum(v * v for v in top.values())) or 1.0
            vectors[e.path] = {s: v / norm for s, v in top.items()}
        postings = defaultdict(list)
        for p, vec in vectors.items():
            for s, w in vec.items():
                postings[s].append((p, w))
        cap = max(50, int(0.3 * n))
        scores = defaultdict(float)
        for s, plist in postings.items():
            if len(plist) < 2 or len(plist) > cap:
                continue
            for i in range(len(plist)):
                pa, wa = plist[i]
                for j in range(i + 1, len(plist)):
                    pb, wb = plist[j]
                    key = (pa, pb) if pa < pb else (pb, pa)
                    scores[key] += wa * wb
        explicit_pairs = set()
        for (a, b, rel), edge in self.edges.items():
            if edge["kind"] == "explicit" and rel in ("links_to", "mentions"):
                explicit_pairs.add((a, b))
                explicit_pairs.add((b, a))
        best = defaultdict(list)
        for (pa, pb), sc in scores.items():
            if sc < SIM_THRESHOLD:
                continue
            best[pa].append((sc, pb))
            best[pb].append((sc, pa))
        keep = set()
        for p, lst in best.items():
            lst.sort(reverse=True)
            for sc, q in lst[:SIM_PER_NODE]:
                keep.add((p, q) if p < q else (q, p))
        for pa, pb in sorted(keep):
            sc = scores[(pa, pb)]
            na, nb = self.node_of_path[pa], self.node_of_path[pb]
            if (na, nb) in explicit_pairs:
                continue
            shared = sorted(set(vectors[pa]) & set(vectors[pb]),
                            key=lambda s: vectors[pa][s] * vectors[pb][s], reverse=True)
            if len(shared) < 2:
                continue
            words = [surface[s].most_common(1)[0][0] for s in shared[:4]]
            ev = []
            for p in (pa, pb):
                e = self.entry_by_path[p]
                off = first_pos[p].get(shared[0])
                if off is not None:
                    li = LineIndex(self.texts[p]["text"])
                    line_no, line = li.evidence(off)
                    ev.append(_ev(e, line_no, line))
            edge = self.add_edge(na, nb, "inferred", "similar_content",
                                 f"İçerik benzerliği %{round(sc * 100)} · ortak terimler: {', '.join(words)}",
                                 None, round(sc, 3))
            if edge is not None:
                edge["evidence"] = ev

        # Anahtar terimlerden konu tahmini
        top_terms = {p: sorted(vec.items(), key=lambda kv: kv[1], reverse=True)[:TOPIC_TOP_TERMS]
                     for p, vec in vectors.items()}
        holders = defaultdict(list)
        for p, terms in top_terms.items():
            for rank, (s, w) in enumerate(terms, 1):
                holders[s].append((p, w, rank))
        candidates = []
        for s, lst in holders.items():
            if len(lst) < 2 or len(s) < 4 or df[s] > max(3, int(0.5 * n)):
                continue
            candidates.append((sum(w for _, w, _ in lst), s))
        candidates.sort(reverse=True)
        k_topics = min(40, max(3, n // 4))
        tag_by_stem = {}
        for nid, node in self.nodes.items():
            if node["type"] == "topic" and node["meta"].get("origin") == "tag":
                tag_by_stem.setdefault(stem(nid.split(":", 1)[1]), nid)
        for _, s in candidates[:k_topics]:
            label = surface[s].most_common(1)[0][0]
            tid = tag_by_stem.get(s)
            if tid:
                self.nodes[tid]["meta"]["origin"] = "tag+keyword"
            else:
                tid = "topic:~" + s
                self.add_node(tid, "topic", label, meta={"origin": "keyword", "stem": s})
            for p, w, rank in holders[s]:
                src = self.node_of_path[p]
                if (src, tid, "tagged") in self.edges:
                    continue
                count = tf[p][s]
                off = first_pos[p].get(s)
                li = LineIndex(self.texts[p]["text"])
                line_no, line = li.evidence(off or 0)
                conf = round(min(0.9, 0.45 + 0.45 * (w / max(1e-9, top_terms[p][0][1]))), 3)
                self.add_edge(src, tid, "inferred", "about_topic",
                              f"‘{label}’ belgede {count} kez geçiyor; öne çıkan {rank}. terim",
                              _ev(self.entry_by_path[p], line_no, line), conf)

    def finish(self):
        degree = Counter()
        for (a, b, _), e in self.edges.items():
            degree[a] += 1
            degree[b] += 1
        for nid, node in self.nodes.items():
            node["degree"] = degree[nid]
        edges = []
        for e in self.edges.values():
            e = dict(e)
            e.pop("count", None)
            edges.append(e)
        return list(self.nodes.values()), edges


def build_index(sources: list[str], db_path: Path, progress=None, max_files: int = 4000,
                time_budget: float = 600.0, opened: list | None = None) -> dict:
    t0 = time.monotonic()
    deadline = t0 + time_budget
    _emit(progress, type="progress", phase="scan", message="Klasörler taranıyor")
    scan = scan_sources(sources, max_files=max_files, deadline=deadline, opened=opened)
    _emit(progress, type="progress", phase="scan_done", files=len(scan.entries))
    cache = store.load_text_cache(db_path)
    texts: dict[str, dict] = {}
    pdfs = []
    reused = extracted = 0
    for i, e in enumerate(scan.entries):
        if e.ext not in ex.TEXT_BEARING:
            continue
        c = cache.get(e.path)
        # Geçici okuma hataları (izin, iCloud, PDF zaman aşımı) önbellekten "boş" diye dönmesin.
        failed_before = str((c or {}).get("method", "")).startswith(("okunamadı", "pdf-okunamadı"))
        if c and not failed_before and c.get("mtime") == e.mtime and c.get("size") == e.size:
            texts[e.path] = c
            reused += 1
            continue
        if e.ext == ".pdf":
            pdfs.append(e)
            continue
        if opened is not None:
            opened.append(e.path)
        text, method = ex.extract_text(Path(e.path))
        texts[e.path] = {"mtime": e.mtime, "size": e.size, "method": method, "text": text,
                         "folded": fold(text)}
        extracted += 1
        if i % 50 == 0:
            _emit(progress, type="progress", phase="extract", done=i, total=len(scan.entries))
    if pdfs:
        _emit(progress, type="progress", phase="pdf", total=len(pdfs))
        if opened is not None:
            opened.extend(e.path for e in pdfs)
        pdf_failed: set = set()
        pdf_texts = ex.extract_pdfs([Path(e.path) for e in pdfs], failed=pdf_failed)
        for e in pdfs:
            t = pdf_texts.get(e.path, "")
            method = "pdf" if t else ("pdf-okunamadı" if e.path in pdf_failed else "pdf-metin-yok")
            texts[e.path] = {"mtime": e.mtime, "size": e.size, "method": method,
                             "text": t, "folded": fold(t)}
            extracted += 1
    _emit(progress, type="progress", phase="graph")
    builder = GraphBuilder(scan, texts)
    builder.build_projects()
    builder.build_files()
    builder.build_explicit_links()
    builder.build_inferred()
    nodes, edges = builder.finish()
    _emit(progress, type="progress", phase="layout", nodes=len(nodes))
    old = store.load_graph(db_path)
    prev = {}
    if old:
        prev = {nid: (n["x"], n["y"]) for nid, n in old["nodes"].items()}
    positions = compute_layout(nodes, edges, prev)
    for node in nodes:
        node["x"], node["y"] = positions.get(node["id"], (0.0, 0.0))
    stats = {
        "sources": scan.roots, "missing_sources": scan.missing_roots,
        "files": len(scan.entries), "nodes": len(nodes), "edges": len(edges),
        "explicit_edges": sum(1 for e in edges if e["kind"] == "explicit"),
        "inferred_edges": sum(1 for e in edges if e["kind"] == "inferred"),
        "by_type": dict(Counter(n["type"] for n in nodes)),
        "truncated": scan.truncated, "skipped": dict(scan.skipped),
        "text_reused": reused, "text_extracted": extracted,
        "seconds": round(time.monotonic() - t0, 2), "built_at": time.time(),
        "model": "yerel TF-IDF benzerliği + anahtar terim (bulut kullanılmaz)",
    }
    store.write_graph(db_path, nodes, edges, texts, stats)
    _emit(progress, type="done", **stats)
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS ikinci beyin indeksleyicisi")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--db", default="")
    parser.add_argument("--max-files", type=int, default=4000)
    args = parser.parse_args(argv)
    try:
        os.nice(10)  # arayüzün akıcılığı için düşük öncelik
    except OSError:
        pass
    from brain import settings
    db = Path(args.db) if args.db else settings.DB_PATH
    sources = args.source or settings.get_sources()

    def progress(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)

    if not sources:
        progress({"type": "error", "code": "needs_setup",
                  "message": "Kaynak klasör seçilmemiş. Ayarlar › İKİNCİ BEYİN › KAYNAKLAR."})
        return 2
    lock = IndexLock(db.with_suffix(".lock"))
    if not lock.acquire():
        progress({"type": "busy", "message": "Başka bir tarama sürüyor."})
        return 3
    try:
        build_index(sources, db, progress=progress, max_files=args.max_files)
        return 0
    except Exception as exc:  # hata ayrıntısı arayüze iletilir, süreç çökmez
        progress({"type": "error", "code": "failed", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
