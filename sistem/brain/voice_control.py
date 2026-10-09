"""JARVIS'in sesli komutlarıyla İkinci Beyin grafiğini yönetme.

Burada yalnızca PLAN yapılır: istenen düğüm/kaynak indeksten bulunur ve grafik penceresine
gönderilecek komutlar üretilir. Dosya açma yalnızca seçili kaynak klasörlerdeki not/dosyalar
için olur (pencere ayrıca denetler). Görünüm sıfırlama bilerek yoktur (kullanıcı isteği:
sıfırlama yalnızca klavyede 0).
"""

from __future__ import annotations

from pathlib import Path

from brain import query, settings
from brain.textutil import fold

CONTROL_ACTIONS = ("select", "filter", "zoom", "fit", "neighbors", "clear", "open_file", "reveal",
                   "hand_control", "close", "rotate", "view_3d", "spin", "focus", "selected")
SELECTED_MEMBERS = 40        # "bunun içinde ne var": en çok bu kadar öğe listelenir
SELECTED_TEXT_CHARS = 1500   # seçili not/dosyadan gönderilen en uzun metin başlangıcı
ROTATE_STEP_DEG = 30
TYPES = ("note", "file", "project", "topic")
TYPE_WORDS = {"not": "note", "notlar": "note", "note": "note", "dosya": "file", "dosyalar": "file",
              "file": "file", "proje": "project", "projeler": "project", "project": "project",
              "konu": "topic", "konular": "topic", "topic": "topic"}


def _node_info(graph: dict, node: dict, limit: int = 6) -> dict:
    if node["type"] == "project":
        explicit, inferred = query.project_view(graph, node["id"], limit)
    else:
        explicit, inferred = query.describe_relations(graph, node["id"], limit)
    return {"title": node["label"], "type": query.TYPE_TR.get(node["type"]), "path": node.get("path", ""),
            "id": node["id"], "explicit": explicit, "inferred": inferred}


def _resolve(graph: dict, text: str, db_path: Path):
    """(düğüm, diğer adaylar) — önce ad/etiket, yoksa metin eşleşmesi."""
    matches = query.match_nodes(graph, text, limit=5)
    if matches and matches[0][0] >= 5.0:
        best = matches[0][1]
        others = [{"title": n["label"], "type": query.TYPE_TR.get(n["type"])}
                  for sc, n in matches[1:4] if sc >= max(8.0, matches[0][0] - 3.0)]
        return best, others
    hits = query.content_matches(graph, text, db_path, limit=3)
    if hits:
        return graph["nodes"].get(hits[0]["id"]), [{"title": h["title"], "type": h["type"]} for h in hits[1:]]
    return None, []


def _source_root(name: str, graph: dict) -> tuple[str | None, list[str]]:
    roots = sorted({n.get("root") for n in graph["nodes"].values() if n.get("root")})
    names = [Path(r).name for r in roots]
    key = fold(name).strip()
    if key in ("", "all", "tum", "tumu", "hepsi", "tum kaynaklar"):
        return "", names
    for r in roots:
        if fold(Path(r).name) == key:
            return r, names
    for r in roots:
        if key in fold(Path(r).name):
            return r, names
    return None, names


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return fold(str(value)).strip() not in ("false", "0", "off", "kapat", "kapali", "hayir")


def plan(action: str, args: dict, db_path: Path | None = None) -> dict:
    """{'result': {...}, 'commands': [...], 'open_window': bool}"""
    action = (action or "").strip().lower()
    db = Path(db_path or settings.DB_PATH)
    text = str(args.get("query", "") or "").strip()
    out = {"result": {"status": "ok", "action": action}, "commands": [], "open_window": True}
    res = out["result"]

    if action == "close":
        out.update(commands=[{"cmd": "quit"}], open_window=False)
        res["message"] = "İkinci Beyin penceresi kapatılıyor."
        return out
    if action == "hand_control":
        enabled = args.get("enabled", True)
        enabled = enabled if isinstance(enabled, bool) else str(enabled).lower() not in ("false", "0", "kapat", "off")
        try:
            settings.set_hand_control(enabled)
        except OSError:
            pass
        out.update(commands=[{"cmd": "hand", "enabled": enabled}], open_window=enabled)
        res.update(enabled=enabled, message=("El kontrolü açıldı; kamera yalnızca grafik penceresi açıkken ve "
                                             "yalnızca Mac'in kendi kamerasıyla çalışır.") if enabled
                   else "El kontrolü kapatıldı; kamera kullanılmıyor.")
        return out

    if action == "view_3d":
        enabled = _flag(args.get("enabled", True))
        try:
            settings.set_view_3d(enabled)
        except OSError:
            pass
        out["commands"] = [{"cmd": "control", "op": "view", "mode3d": enabled}]
        res.update(enabled=enabled, message="Grafik 3 boyutlu gösteriliyor." if enabled
                   else "Grafik 2 boyutlu (düz) gösteriliyor.")
        return out
    if action == "spin":
        enabled = _flag(args.get("enabled", True))
        out["commands"] = [{"cmd": "control", "op": "spin", "on": enabled}]
        res.update(enabled=enabled, message="Grafik kendi kendine yavaşça dönüyor." if enabled
                   else "Otomatik döndürme durduruldu.")
        return out
    if action == "rotate":
        direction = fold(str(args.get("direction", "right") or "right")).strip()
        try:
            steps = max(1, min(6, int(args.get("steps", 1) or 1)))
        except (TypeError, ValueError):
            steps = 1
        angle = ROTATE_STEP_DEG * steps
        # Yön, fareyle o yöne sürüklemek gibidir (öndeki yüz o yöne gider).
        moves = {"left": (angle, 0), "sol": (angle, 0), "sola": (angle, 0),
                 "right": (-angle, 0), "sag": (-angle, 0), "saga": (-angle, 0),
                 "up": (0, -angle), "yukari": (0, -angle), "down": (0, angle), "asagi": (0, angle)}
        if direction not in moves:
            res.update(status="error", message="direction: left, right, up veya down olmalı.")
            return out
        yaw, pitch = moves[direction]
        out["commands"] = [{"cmd": "control", "op": "rotate", "yaw": yaw, "pitch": pitch}]
        res.update(degrees=angle, message=f"Grafik {angle}° döndürüldü.")
        return out

    sources = settings.get_sources()
    if not sources:
        return {"result": {"status": "needs_setup", "action": action,
                           "message": "Kaynak klasör seçilmemiş. Ayarlar › İKİNCİ BEYİN · KAYNAKLAR."},
                "commands": [], "open_window": False}
    graph = query.load(db, sources)
    if graph is None:
        return {"result": {"status": "not_indexed", "action": action,
                           "message": "Seçili klasörler henüz indekslenmedi."},
                "commands": [], "open_window": True}

    if action == "zoom":
        direction = str(args.get("direction", "in") or "in").lower()
        try:
            steps = max(1, min(5, int(args.get("steps", 1) or 1)))
        except (TypeError, ValueError):
            steps = 1
        factor = 1.25 ** steps
        if direction in ("out", "uzaklastir", "uzaklaştır", "disari", "dışarı"):
            factor = 1.0 / factor
            res["message"] = "Uzaklaştırıldı."
        else:
            res["message"] = "Yakınlaştırıldı."
        out["commands"] = [{"cmd": "control", "op": "zoom", "factor": round(factor, 4)}]
        return out
    if action == "fit":
        out["commands"] = [{"cmd": "control", "op": "fit"}]
        res["message"] = "Görünen düğümlerin tümü ekrana sığdırıldı (taşınan düğümler yerinde kalır)."
        return out
    if action == "clear":
        out["commands"] = [{"cmd": "control", "op": "clear"}]
        res["message"] = "Arama, seçim ve komşu odağı temizlendi."
        return out
    if action == "filter":
        cmd = {"cmd": "control", "op": "filter"}
        rel = str(args.get("relations", "") or "").lower()
        if rel:
            if rel not in ("all", "explicit", "inferred"):
                res.update(status="error", message="relations yalnızca all, explicit veya inferred olabilir.")
                return out
            cmd["relations"] = rel
        types = args.get("types")
        if types:
            if isinstance(types, str):
                types = [types]
            chosen = []
            for t in types:
                key = fold(str(t)).strip()
                if key in ("all", "tum", "hepsi"):
                    chosen = list(TYPES)
                    break
                mapped = TYPE_WORDS.get(key) or (key if key in TYPES else None)
                if mapped and mapped not in chosen:
                    chosen.append(mapped)
            if not chosen:
                res.update(status="error", message="types: note, file, project, topic ya da all.")
                return out
            cmd["types"] = chosen
        if args.get("min_confidence") not in (None, ""):
            try:
                conf = float(args.get("min_confidence"))
            except (TypeError, ValueError):
                conf = 0.0
            conf = conf * 100 if 0 < conf <= 1 else conf
            cmd["min_conf"] = int(max(0, min(90, round(conf / 5) * 5)))
        if args.get("source") not in (None, ""):
            root, names = _source_root(str(args.get("source")), graph)
            if root is None:
                res.update(status="error", message="Bu adda bir kaynak klasör yok.", available_sources=names)
                return out
            cmd["source"] = root
        if len(cmd) == 2:
            res.update(status="error", message="Filtre için relations, types, min_confidence veya source ver.")
            return out
        out["commands"] = [cmd]
        res["applied"] = {k: v for k, v in cmd.items() if k not in ("cmd", "op")}
        if "source" in cmd:
            res["applied"]["source"] = Path(cmd["source"]).name if cmd["source"] else "tüm kaynaklar"
        res["message"] = "Filtre uygulandı."
        return out

    if action == "selected":
        return _plan_selected(graph, args.get("_selection"), db, out)

    # düğüm gerektiren işlemler
    node = None
    if text:
        node, others = _resolve(graph, text, db)
        if node is None:
            return {"result": {"status": "not_found", "action": action, "query": text,
                               "message": f"Grafikte ‘{text}’ ile eşleşen düğüm yok (yalnızca seçili klasörler)."},
                    "commands": [], "open_window": False}
        res["node"] = _node_info(graph, node)
        if others:
            res["other_candidates"] = others
    if action == "select":
        if node is None:
            res.update(status="needs_query", message="Hangi not/proje/konu seçilsin? query ile adını ver.")
            return out
        out["commands"] = [{"cmd": "control", "op": "select", "id": node["id"]}]
        res["message"] = f"‘{node['label']}’ seçildi ve ortalandı."
        return out
    if action == "neighbors":
        enabled = args.get("enabled", True)
        enabled = enabled if isinstance(enabled, bool) else str(enabled).lower() not in ("false", "0", "off")
        cmds = [{"cmd": "control", "op": "select", "id": node["id"]}] if node else []
        cmds.append({"cmd": "control", "op": "neighbors", "on": enabled})
        out["commands"] = cmds
        res["message"] = ("Yalnızca seçili düğüm ve doğrudan bağlı olanlar gösteriliyor." if enabled
                          else "Komşu odağı kapatıldı.")
        if enabled and node is None:
            res["note"] = "query verilmedi; penceredeki mevcut seçim kullanılır (seçim yoksa etkisizdir)."
        return out
    if action == "focus":
        enabled = _flag(args.get("enabled", True))
        if not enabled:
            out["commands"] = [{"cmd": "control", "op": "focus", "id": None}]
            res["message"] = "Odaktan çıkılıyor; tüm grafik gösterilecek."
            return out
        if node is None:
            sel = args.get("_selection") or {}
            node = graph["nodes"].get(sel.get("id")) if sel.get("id") else None
            if node is None:
                res.update(status="needs_query", message="Hangi klasöre/nota odaklanılsın? query ile adını ver.")
                return out
            res["node"] = _node_info(graph, node)
            res["note"] = "query verilmedi; grafikte seçili olan kullanıldı."
        out["commands"] = [{"cmd": "control", "op": "focus", "id": node["id"]}]
        res["message"] = (f"‘{node['label']}’ odağına geçiliyor: yalnızca o ve bağlı olanlar gösterilir. "
                          "Çıkmak için ‘odaktan çık’.")
        return out
    if action in ("open_file", "reveal"):
        if node is not None and node["type"] not in ("note", "file") and action == "open_file":
            res.update(status="error", message=f"‘{node['label']}’ bir {query.TYPE_TR.get(node['type'])}; "
                                               "dosya olarak açılamaz. reveal ile Finder'da gösterilebilir.")
            return out
        cmds = [{"cmd": "control", "op": "select", "id": node["id"]}] if node else []
        cmds.append({"cmd": "control", "op": "open" if action == "open_file" else "reveal"})
        out["commands"] = cmds
        res["message"] = ("Dosya açılıyor." if action == "open_file" else "Finder'da gösteriliyor.")
        if node is None:
            res["note"] = "query verilmedi; penceredeki seçili düğüm kullanılır."
        return out
    return {"result": {"status": "error", "action": action, "message": "Bilinmeyen işlem."},
            "commands": [], "open_window": False}


def _plan_selected(graph: dict, selection: dict | None, db: Path, out: dict) -> dict:
    """Kullanıcının grafikte şu an seçtiği/odaklandığı düğüm ve içindekiler ("bunun içinde ne var?")."""
    out["open_window"] = False
    res = out["result"]
    if not selection or not selection.get("id"):
        res.update(status="nothing_selected",
                   message="İkinci Beyin'de şu an seçili bir şey yok (ya da pencere kapalı). "
                           "Kullanıcıya hangi klasörü/notu kastettiğini sor ya da adıyla sor.")
        return out
    node = graph["nodes"].get(selection["id"])
    if node is None:
        res.update(status="not_found", message="Seçili öğe artık indekste yok; yeniden tarama gerekebilir.")
        return out
    info = _node_info(graph, node, limit=8)
    res.update(selected=info, focused=bool(selection.get("focus")),
               seconds_since_selected=selection.get("seconds_ago"),
               note="Kullanıcının 'bu/bunun/şu/seçtiğim' dediği şey budur. Yalnızca buradaki bilgiye dayan; "
                    "dosya içeriğiyle ilgili ayrıntılı soru için read ile dosya adını da içeren bir soru sor.")
    if node["type"] == "project":
        members, projects = query._project_members(graph, node["id"], depth=1)
        items = []
        for mid in members | (projects - {node["id"]}):
            m = graph["nodes"].get(mid)
            if m:
                items.append({"title": m["label"], "type": query.TYPE_TR.get(m["type"], m["type"]),
                              "path": m.get("rel") or m.get("path", "")})
        items.sort(key=lambda i: (i["type"] != "proje", i["title"].lower()))
        res.update(contents_count=len(items), contents=items[:SELECTED_MEMBERS],
                   contents_truncated=len(items) > SELECTED_MEMBERS)
        res["message"] = f"Seçili klasör/proje ‘{node['label']}’: içinde {len(items)} öğe (doğrudan)."
    elif node["type"] in ("note", "file") and node.get("path"):
        from brain import store
        text = store.texts_for_paths(db, [node["path"]]).get(node["path"], "")
        res.update(text_start=text[:SELECTED_TEXT_CHARS], text_chars=len(text),
                   text_truncated=len(text) > SELECTED_TEXT_CHARS)
        res["message"] = (f"Seçili {query.TYPE_TR.get(node['type'])} ‘{node['label']}’."
                          + ("" if text else " Metni indekslenmemiş (ör. görsel ya da taranamayan dosya)."))
    else:
        res["message"] = f"Seçili {query.TYPE_TR.get(node['type'], 'öğe')} ‘{node['label']}’ ve bağlantıları."
    return out
