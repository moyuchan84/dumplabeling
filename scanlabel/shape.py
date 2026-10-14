"""모양 특징과 유사도 — 순수 함수, 표준 라이브러리만 (SPEC.md 「모양 특징」·「유사도」·「제안」).

기록 한 건 = dump raw 스캔 하나(`value{채널: [..]}` + `xyz{x,z}`). 채널마다

- **1D**: 스캔 순서 배열을 `PATH_N` 점으로 줄인 것(구간 평균; 짧으면 선형 보간).
- **2D**: (x, z) 를 0–1 평면에 놓고 `GRID_N`×`GRID_N` 칸 평균(빈 칸은 None).

을 **정규화 전** 값으로 스냅샷에 남긴다. 정규화(z-score)와 비교는 `similarity` 가 그때그때 한다 —
정규화 방식을 바꾸는 변경은 원본 없이 스냅샷만으로 다시 계산된다.

문턱값 · 가중치는 **휴리스틱**이다. `evaluate`(leave-one-out 적중률)로 재고 여기서만 고친다.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional

from .tree import MAX_LEVEL, Tree

FEATURE_VERSION = 1      # 스냅샷의 모양(해상도 · 필드)이 바뀌면 올린다 → jobs refeature
PATH_N = 128
GRID_N = 16

W_1D = 0.5               # 채널 점수 = 가중 평균(있는 쪽만)
W_2D = 0.5
MIN_OVERLAP = 16         # 2D 상관을 낼 최소 공통 칸 수
FLAT_REL = 1e-6          # 범위가 크기의 이 비율 이하면 평평한 신호

SUGGEST_MIN = 0.80       # 이 점수 이상이어야 「제안」
MARGIN = 0.05            # 같은 단계의 2위보다 이만큼 앞서야 「확실」(아니면 갈림)
EVIDENCE_MIN = 0.60      # 근거 건수에 넣는 최소 점수
LEVEL_TOP = 5            # 단계별 순위에 싣는 노드 수


# ─── 값 읽기 ──────────────────────────────────────────────────────────────────

def num(v: Any) -> Optional[float]:
    """측정값 하나. 숫자 문자열은 읽고, `""`·`None`·NaN 은 0 으로 만들지 않고 버린다.

    poc_viewer `scan-dashboard/transform.ts` 의 `num()` 과 같은 규칙 (DUMP-VAL-1 · DUMP-VAL-2).
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        t = v.strip()
        if not t:
            return None
        try:
            f = float(t)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


# ─── 스냅샷 ──────────────────────────────────────────────────────────────────

def _resample(vals: list[float], n: int) -> list[float]:
    m = len(vals)
    if m == 0:
        return []
    if m == 1:
        return [vals[0]] * n
    if m >= n:
        out = []
        for j in range(n):
            a = (j * m) // n
            b = max(a + 1, ((j + 1) * m) // n)
            seg = vals[a:b]
            out.append(sum(seg) / len(seg))
        return out
    out = []
    for j in range(n):
        t = j * (m - 1) / (n - 1)
        k = min(int(t), m - 2)
        f = t - k
        out.append(vals[k] * (1 - f) + vals[k + 1] * f)
    return out


def _stats(vals: list[float]) -> dict:
    n = len(vals)
    if not n:
        return {"n": 0, "min": None, "max": None, "mean": None, "std": None}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return {"n": n, "min": min(vals), "max": max(vals), "mean": mean, "std": math.sqrt(var)}


def _grid(pts: list[tuple[float, float, float]]) -> Optional[list[Optional[float]]]:
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    zs = [p[1] for p in pts]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    if not (x1 > x0 and z1 > z0):
        return None                    # 선이나 점 — 평면 모양이 없다
    sums = [0.0] * (GRID_N * GRID_N)
    cnts = [0] * (GRID_N * GRID_N)
    for x, z, v in pts:
        c = min(GRID_N - 1, int((x - x0) / (x1 - x0) * GRID_N))
        r = min(GRID_N - 1, int((z - z0) / (z1 - z0) * GRID_N))
        sums[r * GRID_N + c] += v
        cnts[r * GRID_N + c] += 1
    return [s / k if k else None for s, k in zip(sums, cnts)]


def channel_features(values: list, xs: list, zs: list) -> dict:
    n = min(len(values), len(xs), len(zs))
    pts: list[tuple[float, float, float]] = []
    for i in range(n):
        v, x, z = num(values[i]), num(xs[i]), num(zs[i])
        if v is not None and x is not None and z is not None:
            pts.append((x, z, v))
    vals = [p[2] for p in pts]
    return {
        "path": [round(v, 9) for v in _resample(vals, PATH_N)] or None,
        "grid": _grid(pts),
        "stats": _stats(vals),
    }


def record_features(scan: Mapping) -> dict:
    """raw 스캔 레코드 → 채널별 스냅샷. 채널 대응은 `value` 의 키 이름만 쓴다."""
    xyz = scan.get("xyz") or {}
    xs = xyz.get("x") or []
    zs = xyz.get("z") or []
    channels = {}
    for key, arr in (scan.get("value") or {}).items():
        if isinstance(arr, list):
            channels[str(key)] = channel_features(arr, xs, zs)
    return {"version": FEATURE_VERSION, "channels": channels}


# ─── 비교 ────────────────────────────────────────────────────────────────────

def _flat(vals: list[float]) -> bool:
    lo, hi = min(vals), max(vals)
    return (hi - lo) <= FLAT_REL * max(abs(lo), abs(hi), 1e-300)


def zscore(vals: list[float]) -> list[float]:
    n = len(vals)
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in vals]


def _corr(a: list[float], b: list[float]) -> float:
    fa, fb = _flat(a), _flat(b)
    if fa or fb:
        return 1.0 if (fa and fb) else 0.0
    za, zb = zscore(a), zscore(b)
    r = sum(p * q for p, q in zip(za, zb)) / len(za)
    return max(-1.0, min(1.0, r))


def _score_1d(a: Mapping, b: Mapping) -> Optional[float]:
    pa, pb = a.get("path"), b.get("path")
    if not pa or not pb or len(pa) != len(pb):
        return None
    return _corr(pa, pb)


def _score_2d(a: Mapping, b: Mapping) -> Optional[float]:
    ga, gb = a.get("grid"), b.get("grid")
    if not ga or not gb or len(ga) != len(gb):
        return None
    both = [(p, q) for p, q in zip(ga, gb) if p is not None and q is not None]
    if len(both) < MIN_OVERLAP:
        return None
    return _corr([p for p, _ in both], [q for _, q in both])


def _scale(a: Mapping, b: Mapping) -> Optional[float]:
    """b 의 크기 / a 의 크기 (표준편차 비). 거리에는 섞지 않고 따로 보여 준다."""
    sa = (a.get("stats") or {}).get("std")
    sb = (b.get("stats") or {}).get("std")
    if not sa or sb is None:
        return None
    return sb / sa


def similarity(a: Mapping, b: Mapping) -> Optional[dict]:
    """두 스냅샷의 유사도. 같은 이름의 채널이 하나도 없거나 비교할 것이 없으면 None."""
    ca, cb = a.get("channels") or {}, b.get("channels") or {}
    by_channel = {}
    for key in ca.keys() & cb.keys():
        s1, s2 = _score_1d(ca[key], cb[key]), _score_2d(ca[key], cb[key])
        parts = [(W_1D, s1), (W_2D, s2)]
        have = [(w, s) for w, s in parts if s is not None]
        if not have:
            continue
        score = sum(w * s for w, s in have) / sum(w for w, _ in have)
        by_channel[key] = {"score": score, "d1": s1, "d2": s2, "scale": _scale(ca[key], cb[key])}
    if not by_channel:
        return None
    vals = list(by_channel.values())

    def mean_of(field: str) -> Optional[float]:
        xs = [v[field] for v in vals if v[field] is not None]
        return sum(xs) / len(xs) if xs else None

    scales = [v["scale"] for v in vals if v["scale"] and v["scale"] > 0]
    return {
        "score": mean_of("score"),
        "d1": mean_of("d1"),
        "d2": mean_of("d2"),
        # 채널마다의 크기 비를 기하평균으로 — ×2 와 ×0.5 가 서로 지운다.
        "scale": math.exp(sum(math.log(s) for s in scales) / len(scales)) if scales else None,
        "by_channel": dict(sorted(by_channel.items())),
    }


def normalized_paths(features: Mapping, keys: Optional[Iterable[str]] = None) -> dict:
    """겹쳐 그리기용 — 채널별 z-score 된 128점 (소수 셋째 자리). 평평하면 0 선."""
    out = {}
    chans = features.get("channels") or {}
    for key in (keys if keys is not None else chans.keys()):
        path = (chans.get(key) or {}).get("path")
        if path:
            out[key] = [round(v, 3) for v in (zscore(path) if not _flat(path) else [0.0] * len(path))]
    return out


# ─── 순위 · 단계별 제안 ──────────────────────────────────────────────────────

def rank(target: Mapping, candidates: Iterable[Mapping], tree: Tree, *, k: int = 10) -> dict:
    """라벨된 기록들을 target 과 비교해 상위 k 건과 단계별 제안을 낸다.

    candidates 의 각 항목은 `features` · `label_ids` 와 호출자가 돌려받고 싶은 필드를 가진다.
    단계별 집계(SPEC.md 「제안」): 라벨된 기록은 자기 노드와 그 조상 전부에 표를 던진다.
    노드 점수 = 그 아래 기록의 최고 점수. 단계 L 은 (1) 그 단계 1위가 `SUGGEST_MIN` 이상이고
    (2) 2위보다 `MARGIN` 이상 앞서고 (3) 단계 L-1 이 확실하며 1위가 그 자식일 때만 확실하다.
    """
    scored = []
    for c in candidates:
        sim = similarity(target, c["features"])
        if sim is None or sim["score"] is None:
            continue
        scored.append((sim, c))
    scored.sort(key=lambda t: -t[0]["score"])

    nodes: dict[int, dict] = {}
    for sim, c in scored:
        for node_id in tree.closure(i for i in c["label_ids"] if int(i) in tree):
            n = nodes.setdefault(node_id, {"score": -1.0, "count": 0})
            n["score"] = max(n["score"], sim["score"])
            if sim["score"] >= EVIDENCE_MIN:
                n["count"] += 1

    levels: dict[int, list[dict]] = {}
    chosen: Optional[int] = None
    for level in range(1, MAX_LEVEL + 1):
        row = sorted(((i, n) for i, n in nodes.items() if int(tree.get(i)["level"]) == level),
                     key=lambda t: (-t[1]["score"], t[0]))
        sure = False
        if row and (level == 1 or chosen is not None):
            top_id, top = row[0]
            second = row[1][1]["score"] if len(row) > 1 else -1.0
            parent = tree.get(top_id)["parent_id"]
            sure = (top["score"] >= SUGGEST_MIN
                    and top["score"] - second >= MARGIN
                    and (level == 1 or (parent is not None and int(parent) == chosen)))
        chosen = row[0][0] if sure else None
        levels[level] = [
            {"label_id": i, "path": tree.path(i), "score": n["score"], "count": n["count"],
             "case_id": tree.case_for(i), "suggested": sure and j == 0}
            for j, (i, n) in enumerate(row[:LEVEL_TOP])
        ]
        if not sure:
            chosen = None

    deepest = None
    for level in range(MAX_LEVEL, 0, -1):
        hit = next((e for e in levels.get(level, []) if e["suggested"]), None)
        if hit:
            deepest = {**hit, "level": level}
            break

    return {
        "matches": [{"similarity": sim, "candidate": c} for sim, c in scored[:k]],
        "levels": levels,
        "suggestion": deepest,
    }


def evaluate(labelled: list[Mapping], tree: Tree, *, same_scan_only: bool = True) -> dict:
    """leave-one-out — 라벨 하나를 빼고 나머지로 맞히는 단계별 top-1 / top-3 적중률.

    정답 = 그 기록의 라벨 closure 중 그 단계의 노드들. 그 단계까지 라벨이 내려가지 않은 기록은
    그 단계의 분모에서 빠진다(중분류에서 멈춘 라벨은 소분류 적중률에 들어가지 않는다).
    """
    out = {level: {"n": 0, "top1": 0, "top3": 0, "suggested": 0, "suggested_right": 0}
           for level in range(1, MAX_LEVEL + 1)}
    for i, item in enumerate(labelled):
        others = [c for j, c in enumerate(labelled) if j != i
                  and (not same_scan_only or c.get("scan_enum", "") == item.get("scan_enum", ""))]
        truth = tree.closure(x for x in item["label_ids"] if int(x) in tree)
        res = rank(item["features"], others, tree, k=0)
        for level in range(1, MAX_LEVEL + 1):
            want = {t for t in truth if int(tree.get(t)["level"]) == level}
            if not want:
                continue
            row = res["levels"].get(level, [])
            o = out[level]
            o["n"] += 1
            if row and row[0]["label_id"] in want:
                o["top1"] += 1
            if any(e["label_id"] in want for e in row[:3]):
                o["top3"] += 1
            if row and row[0]["suggested"]:
                o["suggested"] += 1
                if row[0]["label_id"] in want:
                    o["suggested_right"] += 1
    return out
