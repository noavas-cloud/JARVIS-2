"""İkinci beyin SQLite deposu.

Yazma her zaman geçici dosyaya yapılır ve os.replace ile atomik olarak yerine
konur; okuyucular yarım kalmış bir grafik görmez.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes(
    id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL, path TEXT, rel TEXT,
    root TEXT, size INTEGER, mtime REAL, meta TEXT, x REAL, y REAL, degree INTEGER DEFAULT 0);
CREATE TABLE edges(
    id INTEGER PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
    relation TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT, confidence REAL);
CREATE TABLE texts(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, method TEXT, text TEXT, folded TEXT);
CREATE INDEX edges_src ON edges(src);
CREATE INDEX edges_dst ON edges(dst);
"""


def _connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not Path(db_path).is_file():
        return None
    try:
        # as_uri(): yol "%", "#" veya "?" içerse bile doğru dosya salt okunur açılır.
        con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        con.execute("PRAGMA query_only=ON")
        return con
    except sqlite3.Error:
        return None


def write_graph(db_path: Path, nodes: list[dict], edges: list[dict], texts: dict[str, dict],
                meta: dict) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + f".tmp{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    try:
        con.executescript(SCHEMA)
        full_meta = dict(meta)
        full_meta["schema_version"] = SCHEMA_VERSION
        full_meta.setdefault("built_at", time.time())
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [(k, json.dumps(v, ensure_ascii=False)) for k, v in full_meta.items()])
        con.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(n["id"], n["type"], n["label"], n.get("path", ""), n.get("rel", ""), n.get("root", ""),
              n.get("size"), n.get("mtime"), json.dumps(n.get("meta", {}), ensure_ascii=False),
              n.get("x"), n.get("y"), n.get("degree", 0)) for n in nodes])
        con.executemany(
            "INSERT INTO edges(src,dst,kind,relation,reason,evidence,confidence) VALUES (?,?,?,?,?,?,?)",
            [(e["src"], e["dst"], e["kind"], e["relation"], e["reason"],
              json.dumps(e.get("evidence", []), ensure_ascii=False), e.get("confidence"))
             for e in edges])
        con.executemany(
            "INSERT INTO texts VALUES (?,?,?,?,?,?)",
            [(p, t.get("mtime"), t.get("size"), t.get("method", ""), t.get("text", ""), t.get("folded", ""))
             for p, t in texts.items()])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, db_path)


def load_text_cache(db_path: Path) -> dict[str, dict]:
    con = _connect_ro(db_path)
    if con is None:
        return {}
    try:
        rows = con.execute("SELECT path, mtime, size, method, text, folded FROM texts").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {r[0]: {"mtime": r[1], "size": r[2], "method": r[3], "text": r[4], "folded": r[5]} for r in rows}


def load_meta(db_path: Path) -> dict:
    con = _connect_ro(db_path)
    if con is None:
        return {}
    try:
        return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
    except (sqlite3.Error, ValueError):
        return {}
    finally:
        con.close()


def load_graph(db_path: Path) -> dict | None:
    """{'meta', 'nodes': {id: node}, 'edges': [edge]} ya da indeks yoksa None."""
    con = _connect_ro(db_path)
    if con is None:
        return None
    try:
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        nodes = {}
        for row in con.execute("SELECT id,type,label,path,rel,root,size,mtime,meta,x,y,degree FROM nodes"):
            nodes[row[0]] = {"id": row[0], "type": row[1], "label": row[2], "path": row[3] or "",
                             "rel": row[4] or "", "root": row[5] or "", "size": row[6], "mtime": row[7],
                             "meta": json.loads(row[8] or "{}"), "x": row[9], "y": row[10],
                             "degree": row[11] or 0}
        edges = []
        for row in con.execute("SELECT id,src,dst,kind,relation,reason,evidence,confidence FROM edges"):
            edges.append({"id": row[0], "src": row[1], "dst": row[2], "kind": row[3], "relation": row[4],
                          "reason": row[5], "evidence": json.loads(row[6] or "[]"), "confidence": row[7]})
        return {"meta": meta, "nodes": nodes, "edges": edges}
    except (sqlite3.Error, ValueError):
        return None
    finally:
        con.close()


def search_texts(db_path: Path, folded_terms: list[str], limit: int = 20) -> list[tuple[str, str]]:
    """Katlanmış terimlerin hepsini içeren dosyalar: [(yol, orijinal metin)]."""
    if not folded_terms:
        return []
    con = _connect_ro(db_path)
    if con is None:
        return []
    try:
        where = " AND ".join("instr(folded, ?) > 0" for _ in folded_terms)
        rows = con.execute(f"SELECT path, text FROM texts WHERE {where} LIMIT ?",
                           (*folded_terms, int(limit))).fetchall()
        return [(r[0], r[1]) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def search_texts_ranked(db_path: Path, folded_stems: list[str], limit: int = 15) -> list[tuple[str, str, int]]:
    """Katlanmış köklerden EN AZ BİRİNİ içeren dosyalar, kaç farklı kök içerdiğine göre sıralı:
    [(yol, orijinal metin, eşleşen kök sayısı)]. Soru-cevap için gevşek arama."""
    stems = [s for s in dict.fromkeys(folded_stems) if s][:10]
    if not stems:
        return []
    con = _connect_ro(db_path)
    if con is None:
        return []
    try:
        score = " + ".join("(instr(folded, ?) > 0)" for _ in stems)
        rows = con.execute(f"SELECT path, text, ({score}) AS s FROM texts WHERE s > 0 ORDER BY s DESC LIMIT ?",
                           (*stems, int(limit))).fetchall()
        return [(r[0], r[1], int(r[2])) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def texts_for_paths(db_path: Path, paths: list[str]) -> dict[str, str]:
    paths = list(dict.fromkeys(p for p in paths if p))[:200]
    if not paths:
        return {}
    con = _connect_ro(db_path)
    if con is None:
        return {}
    try:
        marks = ",".join("?" for _ in paths)
        return {r[0]: r[1] for r in con.execute(f"SELECT path, text FROM texts WHERE path IN ({marks})", paths)}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def save_positions(db_path: Path, positions: dict[str, tuple[float, float]]) -> bool:
    """Pencerede elle taşınan düğüm konumlarını kaydetmez; yalnızca yerleşim önbelleği içindir."""
    if not Path(db_path).is_file():
        return False
    try:
        con = sqlite3.connect(str(db_path), timeout=2)
        try:
            con.executemany("UPDATE nodes SET x=?, y=? WHERE id=?",
                            [(x, y, nid) for nid, (x, y) in positions.items()])
            con.commit()
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False
