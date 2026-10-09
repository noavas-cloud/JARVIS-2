"""Kuvvet yönelimli grafik yerleşimi.

İndeksleyici sürecinde (ana arayüzden ayrı, düşük öncelikli) çalışır; pencere
hazır konumları okur. Önceki konumlar varsa sıcak başlangıç yapılır, böylece
yeniden taramada grafik 'zıplamaz'.
"""

from __future__ import annotations

import math
import random

try:
    import numpy as np
except Exception:  # numpy yoksa küçük grafikler için saf Python yolu
    np = None

EDGE_WEIGHT = {"belongs_to": 0.55, "part_of": 0.9, "links_to": 1.0, "mentions": 0.8,
               "tagged": 0.7, "similar_content": 0.6, "about_topic": 0.35}


def _initial_positions(ids, types, edges_by_node, prev, rng):
    pos = {}
    projects = [i for i in ids if types[i] == "project" and i not in prev]
    n = max(1, len(ids))
    radius = 120.0 * math.sqrt(n)
    for k, pid in enumerate(projects):
        a = 2 * math.pi * k / max(1, len(projects))
        pos[pid] = (radius * 0.55 * math.cos(a), radius * 0.55 * math.sin(a))
    for i in ids:
        if i in prev:
            pos[i] = prev[i]
    for i in ids:
        if i in pos:
            continue
        anchors = [pos[j] for j in edges_by_node.get(i, ()) if j in pos]
        if anchors:
            ax = sum(p[0] for p in anchors) / len(anchors)
            ay = sum(p[1] for p in anchors) / len(anchors)
            pos[i] = (ax + rng.uniform(-60, 60), ay + rng.uniform(-60, 60))
        else:
            a = rng.uniform(0, 2 * math.pi)
            r = radius * rng.uniform(0.2, 1.0)
            pos[i] = (r * math.cos(a), r * math.sin(a))
    return pos


def compute_layout(nodes: list[dict], edges: list[dict], prev: dict | None = None,
                   iterations: int | None = None, seed: int = 7) -> dict[str, tuple[float, float]]:
    prev = {k: v for k, v in (prev or {}).items() if v and v[0] is not None and v[1] is not None}
    ids = [n["id"] for n in nodes]
    if not ids:
        return {}
    types = {n["id"]: n["type"] for n in nodes}
    index = {nid: k for k, nid in enumerate(ids)}
    edges_by_node: dict[str, list[str]] = {}
    pairs = []
    for e in edges:
        if e["src"] in index and e["dst"] in index and e["src"] != e["dst"]:
            edges_by_node.setdefault(e["src"], []).append(e["dst"])
            edges_by_node.setdefault(e["dst"], []).append(e["src"])
            w = EDGE_WEIGHT.get(e["relation"], 0.5)
            if e["kind"] == "inferred":
                w *= max(0.3, float(e.get("confidence") or 0.5))
            pairs.append((index[e["src"]], index[e["dst"]], w))
    rng = random.Random(seed)
    start = _initial_positions(ids, types, edges_by_node, prev, rng)
    n = len(ids)
    warm = sum(1 for i in ids if i in prev) / n
    if iterations is None:
        iterations = 50 if warm > 0.9 else (120 if warm > 0.5 else 220)
    if n == 1:
        return {ids[0]: start[ids[0]]}
    if np is not None:
        pos = _layout_numpy(ids, start, pairs, iterations, warm, rng)
    elif n <= 700:
        pos = _layout_python(ids, start, pairs, iterations, warm)
    else:
        pos = start
    return pos


def _layout_numpy(ids, start, pairs, iterations, warm, rng):
    n = len(ids)
    P = np.array([start[i] for i in ids], dtype=np.float32)
    k = 70.0  # ideal kenar uzunluğu (dünya birimi)
    if pairs:
        src = np.array([p[0] for p in pairs])
        dst = np.array([p[1] for p in pairs])
        w = np.array([p[2] for p in pairs], dtype=np.float32)
    temp0 = k * (1.5 if warm > 0.5 else 6.0)
    sample = None
    if n > 900:  # büyük grafikte itmeyi rastgele örneklemle yaklaşıkla (yansız tahmin, çok daha hızlı)
        sample = 600
    for it in range(iterations):
        temp = temp0 * (1.0 - it / iterations) + 0.5
        disp = np.zeros_like(P)
        if sample is None:
            for s in range(0, n, 600):  # bellek için parçalı hesap
                block = P[s:s + 600]
                delta = block[:, None, :] - P[None, :, :]
                d2 = (delta ** 2).sum(-1) + 1e-2
                force = (k * k) / d2
                idx = np.arange(s, min(n, s + 600))
                force[np.arange(len(idx)), idx] = 0.0
                disp[s:s + 600] += (delta * force[:, :, None]).sum(1)
        else:
            pick = np.array(rng.sample(range(n), sample))
            delta = P[:, None, :] - P[None, pick, :]
            d2 = (delta ** 2).sum(-1) + 1e-2
            force = (k * k) / d2 * (n / sample)
            disp += (delta * force[:, :, None]).sum(1)
        if pairs:
            delta = P[src] - P[dst]
            dist = np.sqrt((delta ** 2).sum(-1)) + 1e-6
            vec = delta * (w * dist / k)[:, None]  # Fruchterman-Reingold çekimi: w·d²/k
            np.add.at(disp, src, -vec)
            np.add.at(disp, dst, vec)
        disp -= P * 0.012  # merkeze hafif çekim: kopuk parçalar uzaklaşmasın
        length = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        P += disp / length[:, None] * np.minimum(length, temp)[:, None]
    return {ids[i]: (float(P[i, 0]), float(P[i, 1])) for i in range(n)}


def _layout_python(ids, start, pairs, iterations, warm):
    n = len(ids)
    X = [start[i][0] for i in ids]
    Y = [start[i][1] for i in ids]
    k = 70.0
    temp0 = k * (1.5 if warm > 0.5 else 6.0)
    for it in range(iterations):
        temp = temp0 * (1.0 - it / iterations) + 0.5
        dx = [0.0] * n
        dy = [0.0] * n
        for a in range(n):
            xa, ya = X[a], Y[a]
            for b in range(a + 1, n):
                ex, ey = xa - X[b], ya - Y[b]
                d2 = ex * ex + ey * ey + 1e-2
                f = k * k / d2
                dx[a] += ex * f
                dy[a] += ey * f
                dx[b] -= ex * f
                dy[b] -= ey * f
        for a, b, w in pairs:
            ex, ey = X[a] - X[b], Y[a] - Y[b]
            d = math.hypot(ex, ey) + 1e-6
            f = w * d / k
            dx[a] -= ex * f
            dy[a] -= ey * f
            dx[b] += ex * f
            dy[b] += ey * f
        for i in range(n):
            dx[i] -= X[i] * 0.012
            dy[i] -= Y[i] * 0.012
            length = math.hypot(dx[i], dy[i]) + 1e-9
            step = min(length, temp)
            X[i] += dx[i] / length * step
            Y[i] += dy[i] / length * step
    return {ids[i]: (X[i], Y[i]) for i in range(n)}


def assign_depth(positions: dict[str, tuple[float, float]], edges: list[dict],
                 types: dict[str, str] | None = None, iterations: int = 14) -> dict[str, float]:
    """3B görünüm için her düğüme derinlik (z) verir; (x, y) yerleşimine dokunmaz.

    Başlangıç derinliği düğüm kimliğinden türetilir (her açılışta aynı sonuç). Ardından
    bağlantılı düğümler derinlikte birbirine çekilir (Laplace yumuşatması); böylece kümeler
    tutarlı katmanlar oluşturur ve döndürünce yapı belirginleşir. Sonuç, grafiğin düzlemdeki
    yayılımının ~%40'ı kadar standart sapmaya ölçeklenir.
    """
    import zlib

    ids = [i for i, p in positions.items() if p is not None]
    if not ids:
        return {}
    xs = [positions[i][0] or 0.0 for i in ids]
    ys = [positions[i][1] or 0.0 for i in ids]
    mx, my = sum(xs) / len(ids), sum(ys) / len(ids)
    rms = math.sqrt(sum((x - mx) ** 2 + (y - my) ** 2 for x, y in zip(xs, ys)) / len(ids)) or 100.0

    def unit(nid: str, salt: bytes) -> float:
        return zlib.crc32(salt + nid.encode("utf-8")) / 4294967295.0 * 2.0 - 1.0

    z = {}
    for nid in ids:
        z[nid] = unit(nid, b"z")
        if types and types.get(nid) == "project":
            z[nid] *= 0.35          # projeler (merkezler) ortaya yakın dursun
    nbrs: dict[str, list[str]] = {}
    for e in edges:
        a, b = e.get("src"), e.get("dst")
        if a in z and b in z and a != b:
            nbrs.setdefault(a, []).append(b)
            nbrs.setdefault(b, []).append(a)
    for _ in range(max(0, iterations)):
        z = {nid: (0.5 * z[nid] + 0.5 * sum(z[j] for j in ns) / len(ns)) if (ns := nbrs.get(nid)) else z[nid]
             for nid in ids}
    # Sıra tabanlı ölçekleme: bağlantısız tek düğümler aşırı uçlara fırlamasın (sağlam dağılım).
    order = sorted(ids, key=lambda nid: (z[nid], nid))
    n = len(order)
    half = 0.70 * rms          # tekdüze [-half, half] → std ≈ 0.40 × yayılım
    jitter = 0.08 * rms        # küme içindeki düğümler aynı düzlemde kalmasın
    return {nid: ((k + 0.5) / n * 2.0 - 1.0) * half + unit(nid, b"j") * jitter
            for k, nid in enumerate(order)}
