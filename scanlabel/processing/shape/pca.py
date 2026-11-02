"""주성분(PCA) — 묶음 · 지도의 두 번째 잣대 (SL-CLU-10)."""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping, Optional

from .constants import CLUSTER_MIN_SIZE, GRID_N, PCA_FLOOR, PCA_GRID, PCA_ITER, PCA_MAT_W, PCA_MAX_K, PCA_PATH, PCA_VAR, PCA_W
from .skew import _skew_norm
from .similarity import _dot


#
# 한 묶음 범위 안에서 기록마다 세 가지 「평소와 다른 정도」를 한 줄로 늘어놓고 주성분으로 줄인다.
#   shape — 채널마다 (제 표준편차로 정규화한 값) − 범위의 같은 채널 · 같은 자리 중앙값: 같은 항목끼리의 차이
#   skew  — 자리마다 채널 간 편차 − 범위의 중앙값: `skew` 잣대와 같은 것
#   amp   — 채널마다 log(표준편차) − 범위의 중앙값: 진폭(크기)이 평소와 다른 정도
# 몫(`PCA_W`)은 값 하나당 RMS 로 맞춘다 — 범위가 고르면 벗어남도 작게 남는다(범위마다 다시 키우지 않는다).

_PCA_MATS = {"area": ("x", "z", "grid", "path"), "line": ("path",)}


def _model_mats(model: Mapping) -> tuple[str, ...]:
    """투영식이 쓴 재료 — 저장된 순서대로. SL-CLU-11 전에 저장된 면 스캔 투영식은 측정 순서 곡선이 없다."""
    return tuple(b["mat"] for b in model["layout"] if b["part"] == "shape")


def _down_grid(g: list) -> list[Optional[float]]:
    f = GRID_N // PCA_GRID
    out: list[Optional[float]] = []
    for r in range(PCA_GRID):
        for c in range(PCA_GRID):
            have = [g[(r * f + i) * GRID_N + c * f + j] for i in range(f) for j in range(f)]
            have = [v for v in have if v is not None]
            out.append(sum(have) / len(have) if have else None)
    return out


def _down(seq: list, n: int) -> list[Optional[float]]:
    f = max(1, len(seq) // n)
    out: list[Optional[float]] = []
    for j in range(0, len(seq) - f + 1, f):
        have = [v for v in seq[j:j + f] if v is not None]
        out.append(sum(have) / len(have) if have else None)
    return out


def _pca_raw(features: Mapping, mats: Optional[tuple[str, ...]] = None
             ) -> Optional[tuple[str, tuple[str, ...], list[tuple[str, str, list]]]]:
    """기록 하나의 재료 — (종류, 채널들, [(part, mat, 값들)]). 값은 채널 순(이름 순)으로 이어 붙인다.

    `mats` 를 주면(저장된 투영식의 재료) 그 재료만 그 순서로. 측정 순서 곡선은 늘 `PCA_PATH` 칸 — 점이 모자라
    더 짧으면 빈 자리(평소)로 채운다.
    """
    kind, norm = _skew_norm(features)
    if kind is None:
        return None
    keys = tuple(sorted(norm))
    chans = features.get("channels") or {}
    use = [m for m in (mats or _PCA_MATS[kind]) if m in _PCA_MATS[kind]]
    blocks: list[tuple[str, str, list]] = []
    seqs: dict[str, list[list]] = {}
    for m in use:
        if m == "path":
            seqs[m] = [(_down(norm[k]["path"], PCA_PATH) + [None] * PCA_PATH)[:PCA_PATH] for k in keys]
        else:
            seqs[m] = [_down_grid(norm[k]["grid"]) if m == "grid" else norm[k][m] for k in keys]
        blocks.append(("shape", m, [v for s in seqs[m] for v in s]))
    for m in use:
        dev: list[list[Optional[float]]] = [[None] * len(seqs[m][0]) for _ in keys]
        for j in range(len(seqs[m][0])):
            vals = [s[j] for s in seqs[m]]
            if any(v is None for v in vals):
                continue
            mu = sum(vals) / len(vals)
            for i, v in enumerate(vals):
                dev[i][j] = v - mu
        blocks.append(("skew", m, [v for d in dev for v in d]))
    amp = []
    for k in keys:
        std = ((chans.get(k) or {}).get("stats") or {}).get("std")
        amp.append(math.log(std) if std and std > 0 else None)
    blocks.append(("amp", "", amp))
    return kind, keys, blocks


def _pca_resid(model: Mapping, blocks: list[tuple[str, str, list]]) -> list[float]:
    """재료 → 가중한 「평소와 다른 정도」 한 줄 (가운데로 옮기기 전). 빈 자리는 평소(0)."""
    out: list[float] = []
    pos = 0
    for (_, _, vals), w in zip(blocks, model["factor"]):
        med = model["median"][pos:pos + len(vals)]
        out += [0.0 if v is None else (v - m) * w for v, m in zip(vals, med)]
        pos += len(vals)
    return out


def _orth(vs: list[list[float]]) -> list[list[float]]:
    """Gram–Schmidt (두 번) — 거의 0 인 벡터는 0 으로 남긴다(계수가 모자란 범위)."""
    out: list[list[float]] = []
    for v in vs:
        w = v[:]
        for _ in range(2):
            for u in out:
                d = _dot(u, w)
                if d:
                    w = [a - d * b for a, b in zip(w, u)]
        nrm = math.sqrt(_dot(w, w))
        out.append([a / nrm for a in w] if nrm > 1e-12 else [0.0] * len(w))
    return out


def _jacobi(a: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    """작은 대칭 행렬의 고윳값 · 고유벡터 (Jacobi 회전). 큰 것부터. 벡터는 행렬의 기저 좌표."""
    k = len(a)
    a = [r[:] for r in a]
    v = [[float(i == j) for j in range(k)] for i in range(k)]
    for _ in range(100):
        if sum(a[i][j] ** 2 for i in range(k) for j in range(k) if i != j) < 1e-24:
            break
        for p in range(k):
            for q in range(p + 1, k):
                if abs(a[p][q]) < 1e-300:
                    continue
                th = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if th >= 0 else -1.0) / (abs(th) + math.sqrt(th * th + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for r in range(k):
                    arp, arq = a[r][p], a[r][q]
                    a[r][p], a[r][q] = c * arp - s * arq, s * arp + c * arq
                for r in range(k):
                    apr, aqr = a[p][r], a[q][r]
                    a[p][r], a[q][r] = c * apr - s * aqr, s * apr + c * aqr
                for r in range(k):
                    vrp, vrq = v[r][p], v[r][q]
                    v[r][p], v[r][q] = c * vrp - s * vrq, s * vrp + c * vrq
    order = sorted(range(k), key=lambda i: (-a[i][i], i))
    return [a[i][i] for i in order], [[v[r][i] for r in range(k)] for i in order]


def _top_pcs(rows: list[list[float]], k: int) -> tuple[list[float], list[list[float]]]:
    """가운데로 옮긴 행들의 큰 주성분 k 개 — 부분공간 반복 + Rayleigh–Ritz. 표준 라이브러리만, 결정적.

    공분산을 만들지 않고 Xᵀ(Xv) 로 곱한다 — 기록 n · 피처 d 일 때 한 번에 k·(n + d) 번의 내적.
    """
    n, d = len(rows), len(rows[0])
    cols = [list(c) for c in zip(*rows)]

    def cov(v: list[float]) -> list[float]:
        xv = [_dot(r, v) for r in rows]
        return [_dot(c, xv) / n for c in cols]
    q = _orth([[math.sin((i + 1) * (j + 1.37)) for i in range(d)] for j in range(k)])
    lam = [0.0] * k
    for _ in range(PCA_ITER):
        z = [cov(v) for v in q]
        new = [_dot(a, b) for a, b in zip(q, z)]
        q = _orth(z)
        done = max(abs(a - b) for a, b in zip(new, lam)) <= 1e-9 * max(max(new), 1e-300)
        lam = new
        if done:
            break
    z = [cov(v) for v in q]
    vals, vecs = _jacobi([[_dot(q[a], z[b]) for b in range(k)] for a in range(k)])
    comps = []
    for vec in vecs:
        c = [sum(vec[a] * q[a][i] for a in range(k)) for i in range(d)]
        big = max(range(d), key=lambda i: abs(c[i]))        # 부호를 정한다 — 가장 큰 값이 양수
        comps.append([-x for x in c] if c[big] < 0 else c)
    return [max(0.0, x) for x in vals], comps


def pca_model(features_list: Iterable[Mapping]) -> Optional[dict]:
    """묶음 범위 하나의 주성분 투영식 (SL-CLU-10). 기록이 `CLUSTER_MIN_SIZE` 보다 적으면 None.

    가장 흔한 (종류, 채널 집합)의 기록으로만 낸다 — 다른 기록은 이 범위의 주성분에 놓이지 않는다
    (`pca_coords` 가 None). 같은 투영식으로 결과에 없던 기록도 같은 자리에 놓는다(정확한 선형 투영).
    """
    raws = [_pca_raw(f) for f in features_list]
    sets = [(r[0], r[1]) for r in raws if r is not None]
    if not sets:
        return None
    (kind, keys), _ = max(((s, sets.count(s)) for s in set(sets)), key=lambda t: (t[1], t[0]))
    same = [r[2] for r in raws if r is not None and (r[0], r[1]) == (kind, keys)]
    n = len(same)
    if n < CLUSTER_MIN_SIZE:
        return None
    layout, median, factor = [], [], []
    for b, (part, mat, vals) in enumerate(same[0]):
        layout.append({"part": part, "mat": mat, "len": len(vals)})
        for j in range(len(vals)):
            have = [s[b][2][j] for s in same if s[b][2][j] is not None]
            median.append(round(statistics.median(have), 6) if have else 0.0)
        w = PCA_W[part] * (PCA_MAT_W[kind][mat] if part != "amp" else 1.0)
        factor.append(math.sqrt(w / len(vals)) if vals else 0.0)
    model: dict[str, Any] = {"method": "pca", "kind": kind, "keys": list(keys), "layout": layout,
                             "median": median, "factor": factor}
    rows = [_pca_resid(model, s) for s in same]
    d = len(rows[0])
    mean = [sum(r[i] for r in rows) / n for i in range(d)]
    centred = [[x - m for x, m in zip(r, mean)] for r in rows]
    total = sum(_dot(r, r) for r in centred) / n
    k0 = min(PCA_MAX_K, n - 1, d)
    var, comps = _top_pcs(centred, k0) if total > 1e-18 and k0 > 0 else ([], [])
    keep, acc = 0, 0.0
    for v in var:
        if v <= 1e-12 * max(total, 1e-300) or (keep >= 2 and acc >= PCA_VAR * total):
            break
        keep += 1
        acc += v
    model.update(mean=[round(x, 6) for x in mean], total=round(total, 9), n=n,
                 var=[round(v, 9) for v in var[:keep]],
                 components=[[round(x, 6) for x in c] for c in comps[:keep]])
    return model


def pca_coords(model: Mapping, features: Mapping) -> Optional[list[float]]:
    """기록 하나의 주성분 좌표. 종류 · 채널 집합이 투영식과 다르면 None."""
    raw = _pca_raw(features, _model_mats(model))
    if raw is None or raw[0] != model["kind"] or list(raw[1]) != model["keys"]:
        return None
    x = [r - m for r, m in zip(_pca_resid(model, raw[2]), model["mean"])]
    return [_dot(c, x) for c in model["components"]]


def pca_similarity(u: Optional[list[float]], v: Optional[list[float]]) -> Optional[float]:
    """주성분 좌표의 닮음 = (2·u·v + f) / (|u|² + |v|² + f), f = 2·PCA_FLOOR² — `skew` 잣대와 같은 꼴.

    좌표는 범위의 평균에서 잰다: 자리 · 방향 · 크기가 같아야 1, 둘 다 평균 곁이면 1 쪽, 반대면 −1 쪽.
    """
    if u is None or v is None:
        return None
    f = 2.0 * PCA_FLOOR * PCA_FLOOR
    return max(-1.0, min(1.0, (2.0 * _dot(u, v) + f) / (_dot(u, u) + _dot(v, v) + f)))


def _pca_split(model: Mapping, vec: list[float]):
    """피처 줄 → (part, mat, 채널, 값들) 로 다시 나눈다 — 축 읽기 · 기록 읽기용."""
    keys = model["keys"]
    pos = 0
    for blk in model["layout"]:
        seg = vec[pos:pos + blk["len"]]
        pos += blk["len"]
        per = blk["len"] // len(keys)
        for i, k in enumerate(keys):
            yield blk["part"], blk["mat"], k, seg[i * per:(i + 1) * per]


def _shares(model: Mapping, vec: list[float]) -> list[dict]:
    acc: dict[tuple[str, str], float] = {}
    for part, _, k, vals in _pca_split(model, vec):
        acc[(part, k)] = acc.get((part, k), 0.0) + _dot(vals, vals)
    tot = sum(acc.values())
    out = [{"part": p, "channel": k, "share": round(s / tot, 3) if tot else 0.0} for (p, k), s in acc.items()]
    return sorted(out, key=lambda e: (-e["share"], e["part"], e["channel"]))


def pca_axes(model: Mapping, n: int = 2) -> dict:
    """지도의 축 읽기 — 축마다 설명하는 분산의 몫, (재료, 채널)별 무게의 몫, 1D 재료의 무게 곡선.

    무게(loading)가 큰 자리일수록 그 축을 따라 기록이 갈린다. 곡선은 X · Z 단면과 측정 순서 곡선(면,
    SL-CLU-11) 또는 측정 순서 곡선(선)만 — 칸은 몫에만 넣는다.
    """
    total = model.get("total") or 0.0
    axes = []
    for a, comp in enumerate(model["components"][:n]):
        lines: dict[str, dict[str, dict]] = {"shape": {}, "skew": {}}
        amp: dict[str, float] = {}
        for part, mat, k, vals in _pca_split(model, comp):
            if part == "amp":
                amp[k] = round(vals[0], 4)
            elif mat != "grid":
                lines[part].setdefault(k, {})["line" if mat == "path" else mat] = [round(x, 4) for x in vals]
        axes.append({"axis": a + 1, "explained": round(model["var"][a] / total, 3) if total else 0.0,
                     "parts": _shares(model, comp), "loadings": lines, "amp": amp})
    return {"axes": axes, "kept": len(model["components"]),
            "explained": round(sum(model["var"]) / total, 3) if total else 0.0, "n": model.get("n")}


def pca_parts(model: Mapping, features: Mapping) -> Optional[dict]:
    """기록 하나가 범위의 평소와 어디서 다른가 — 벗어남(값 하나당 RMS)과 (재료, 채널)별 몫."""
    raw = _pca_raw(features, _model_mats(model))
    if raw is None or raw[0] != model["kind"] or list(raw[1]) != model["keys"]:
        return None
    vec = _pca_resid(model, raw[2])
    return {"distance": round(math.sqrt(_dot(vec, vec)), 4), "parts": _shares(model, vec)}
