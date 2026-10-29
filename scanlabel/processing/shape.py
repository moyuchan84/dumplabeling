"""모양 특징과 유사도 — 순수 함수, 표준 라이브러리만 (SPEC.md 「모양 특징」·「유사도」·「제안」).

기록 한 건 = dump raw 스캔 하나(`value{채널: [..]}` + `xyz{x,z}`). 채널마다

- **측정 순서 곡선**: 스캔 순서 배열을 `PATH_N` 점으로 줄인 것(구간 평균; 짧으면 선형 보간).
- **칸**: (x, z) 를 0–1 평면에 놓고 `GRID_N`×`GRID_N` 칸 평균(빈 칸은 None).

을 **정규화 전** 값으로 스냅샷에 남긴다. 비교 재료 고르기 · 정규화(z-score) · 비교는 `similarity` 가
그때그때 한다 — 그 방식을 바꾸는 변경은 원본 없이 스냅샷만으로 다시 계산된다.

- 면 스캔(칸이 있다): 1D = **X 단면 · Z 단면**(칸의 열 · 행 평균), 2D = 칸. 셋을 **각자** 정규화한다.
  측정 순서 곡선은 쓰지 않는다 — 면을 한 줄로 펴면 행이 꺾이는 자리가 섞여 모양이 뭉개진다.
- 선 스캔(칸이 없다): 1D = 측정 순서 곡선.

문턱값 · 가중치는 **휴리스틱**이다. `evaluate`(leave-one-out 적중률)로 재고 여기서만 고친다.
"""
from __future__ import annotations

import math
import operator
from typing import Any, Iterable, Mapping, Optional

from .tree import MAX_LEVEL, Tree

FEATURE_VERSION = 1      # 스냅샷의 모양(해상도 · 필드)이 바뀌면 올린다 → jobs refeature
PATH_N = 128
GRID_N = 16

W_1D = 0.5               # 채널 점수 = 가중 평균(있는 쪽만)
W_2D = 0.5
MIN_OVERLAP = 16         # 2D 상관을 낼 최소 공통 칸 수
PROFILE_MIN = 8          # 단면(X · Z) 상관을 낼 최소 공통 자리 수 (단면 길이 = GRID_N)
FLAT_REL = 1e-6          # 범위가 크기의 이 비율 이하면 평평한 신호

SUGGEST_MIN = 0.80       # 이 점수 이상이어야 「제안」
MARGIN = 0.05            # 같은 단계의 2위보다 이만큼 앞서야 「확실」(아니면 갈림)
EVIDENCE_MIN = 0.60      # 근거 건수에 넣는 최소 점수
LEVEL_TOP = 5            # 단계별 순위에 싣는 노드 수
CASE_TOP = 5             # 유사 사례 목록에 싣는 사례 수

CLUSTER_MIN = 0.80       # 묶음의 대표 기록과 이 점수 이상이어야 그 묶음의 구성원
CLUSTER_MIN_SIZE = 3     # 이보다 작은 무리는 묶음이 아니다(「묶이지 않음」)
CLUSTER_MAX_N = 800      # 묶음 범위(스캔 종류 · 센서) 하나에서 묶는 최대 기록 수(최근 것부터) — 쌍 비교가 n² 이다
CLUSTER_AGREE = 0.60     # 라벨된 구성원 중 이 비율 이상이 같은 노드여야 그 묶음의 분류
MAP_LANDMARKS = 24       # 묶음 지도의 기준점 수 — 기록마다 이만큼만 비교해 2D 자리를 정한다


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


def _zcorr(za: list[float], zb: list[float]) -> float:
    """z-score 된 두 배열의 상관 — `_corr` 의 뒷부분. 미리 정규화해 둔 쪽(prepare)이 이것만 부른다."""
    r = _dot(za, zb) / len(za)
    return max(-1.0, min(1.0, r))


# math.sumprod(3.12+)는 C 에서 곱해 더한다 — 묶음의 n² 비교에서 대부분의 시간이 여기다.
_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(map(operator.mul, a, b)))


def _corr(a: list[float], b: list[float]) -> float:
    fa, fb = _flat(a), _flat(b)
    if fa or fb:
        return 1.0 if (fa and fb) else 0.0
    return _zcorr(zscore(a), zscore(b))


def profiles(grid: Optional[list]) -> Optional[dict]:
    """2D 칸 평균 → 두 방향의 단면: x = 열마다의 평균(Z 로 평균), z = 행마다의 평균(X 로 평균).

    면 스캔을 측정 순서대로 한 줄로 펴면 행이 꺾이는 자리가 구간 평균에 섞여 모양이 뭉개진다 — 단면은
    측정 순서와 무관하다. 스냅샷의 칸에서 만들므로 원본 없이 다시 계산된다. 빈 줄은 None.
    """
    if not grid or len(grid) != GRID_N * GRID_N:
        return None

    def mean(vals: list) -> Optional[float]:
        have = [v for v in vals if v is not None]
        return sum(have) / len(have) if have else None

    return {"x": [mean([grid[r * GRID_N + c] for r in range(GRID_N)]) for c in range(GRID_N)],
            "z": [mean(grid[r * GRID_N:(r + 1) * GRID_N]) for r in range(GRID_N)]}


# 채널의 비교 재료: 선 스캔은 측정 순서 곡선(path) 하나, 면 스캔은 X 단면 · Z 단면 · 칸 — 서로 따로 정규화한다.
_SEQ_MIN = {"path": 2, "x": PROFILE_MIN, "z": PROFILE_MIN, "grid": MIN_OVERLAP}


def _pre(vals: list, min_n: int) -> Optional[tuple[bool, Optional[list[float]]]]:
    """빈 자리 없는 배열은 (평평한가, z-score) 를 미리 — 빈 자리가 있으면 공통 자리가 상대마다 달라 그때그때."""
    if len(vals) < min_n or any(v is None for v in vals):
        return None
    flat = _flat(vals)
    return flat, None if flat else zscore(vals)


def _prep_channel(ch: Mapping) -> dict:
    grid = ch.get("grid")
    p: dict[str, Any] = {"stats": ch.get("stats"), "two_d": bool(grid), "seq": {}, "pre": {}}
    if grid:
        p["seq"] = {"grid": grid, **(profiles(grid) or {})}
    elif ch.get("path"):
        p["seq"] = {"path": ch["path"]}
    for k, vals in p["seq"].items():
        p["pre"][k] = _pre(vals, _SEQ_MIN[k])
    return p


def prepare(features: Mapping) -> dict:
    """비교 준비 — 채널마다 재료를 고르고 z-score 를 한 번만 한다. `similarity` 에 스냅샷 대신 넣을 수 있다.

    값은 스냅샷 그대로 비교한 것과 **같다**: 빈 자리가 없는 배열만 미리 정규화한다(빈 자리가 있으면 공통
    자리가 상대마다 달라 그때그때 정규화해야 한다). 묶음처럼 한 기록을 수백 번 비교할 때 쓴다.
    """
    if features.get("_prepared"):
        return dict(features)
    return {"_prepared": True,
            "channels": {k: _prep_channel(ch) for k, ch in (features.get("channels") or {}).items()}}


def _score_seq(a: Mapping, b: Mapping, key: str) -> Optional[float]:
    """같은 재료 하나의 상관 — 양쪽을 **각자** z-score 한 뒤 Pearson. 빈 자리는 공통 자리만."""
    sa, sb = a["seq"].get(key), b["seq"].get(key)
    if not sa or not sb or len(sa) != len(sb):
        return None
    pa, pb = a["pre"].get(key), b["pre"].get(key)
    if pa is not None and pb is not None:
        if pa[0] or pb[0]:
            return 1.0 if (pa[0] and pb[0]) else 0.0
        return _zcorr(pa[1], pb[1])
    both = [(p, q) for p, q in zip(sa, sb) if p is not None and q is not None]
    if len(both) < _SEQ_MIN[key]:
        return None
    return _corr([p for p, _ in both], [q for _, q in both])


def _scale(a: Mapping, b: Mapping) -> Optional[float]:
    """b 의 크기 / a 의 크기 (표준편차 비). 거리에는 섞지 않고 따로 보여 준다."""
    sa = (a.get("stats") or {}).get("std")
    sb = (b.get("stats") or {}).get("std")
    if not sa or sb is None:
        return None
    return sb / sa


def _mean(xs: Iterable[Optional[float]]) -> Optional[float]:
    have = [x for x in xs if x is not None]
    return sum(have) / len(have) if have else None


def _channel_score(a: Mapping, b: Mapping) -> Optional[dict]:
    """채널 하나 — 면 스캔끼리: 1D = X · Z 단면 상관의 평균, 2D = 칸 상관. 선 스캔끼리: 1D = 측정 순서 곡선.
    면과 선은 모양의 종류가 달라 비교하지 않는다."""
    if a["two_d"] != b["two_d"]:
        return None
    if a["two_d"]:
        dx, dz = _score_seq(a, b, "x"), _score_seq(a, b, "z")
        s1, s2 = _mean((dx, dz)), _score_seq(a, b, "grid")
    else:
        dx = dz = s2 = None
        s1 = _score_seq(a, b, "path")
    have = [(w, s) for w, s in ((W_1D, s1), (W_2D, s2)) if s is not None]
    if not have:
        return None
    return {"score": sum(w * s for w, s in have) / sum(w for w, _ in have),
            "d1": s1, "d2": s2, "dx": dx, "dz": dz, "scale": _scale(a, b)}


def similarity(a: Mapping, b: Mapping) -> Optional[dict]:
    """두 스냅샷(또는 `prepare` 한 것)의 유사도. 같은 이름의 채널이 하나도 없거나 비교할 것이 없으면 None."""
    ca, cb = prepare(a)["channels"], prepare(b)["channels"]
    by_channel = {}
    for key in ca.keys() & cb.keys():
        s = _channel_score(ca[key], cb[key])
        if s is not None:
            by_channel[key] = s
    if not by_channel:
        return None
    vals = list(by_channel.values())
    scales = [v["scale"] for v in vals if v["scale"] and v["scale"] > 0]
    return {
        "score": _mean(v["score"] for v in vals),
        "d1": _mean(v["d1"] for v in vals),
        "d2": _mean(v["d2"] for v in vals),
        # 채널마다의 크기 비를 기하평균으로 — ×2 와 ×0.5 가 서로 지운다.
        "scale": math.exp(sum(math.log(s) for s in scales) / len(scales)) if scales else None,
        "by_channel": dict(sorted(by_channel.items())),
    }


def _znone(vals: list) -> list[Optional[float]]:
    """빈 자리(None)는 두고 있는 값만 z-score — 그림용, 소수 셋째 자리. 평평하면 0 선."""
    have = [v for v in vals if v is not None]
    if not have:
        return [None] * len(vals)
    z = iter(zscore(have) if not _flat(have) else [0.0] * len(have))
    return [None if v is None else round(next(z), 3) for v in vals]


def normalized_profiles(features: Mapping, keys: Optional[Iterable[str]] = None, *,
                        grid: bool = False) -> dict:
    """겹쳐 그리기용 — 채널별로 비교에 쓰는 재료를 **각자** z-score 한 것.

    면 스캔: `{"x": [16], "z": [16]}` (+ `grid=True` 면 `"grid": [256]`, 행 = Z · 열 = X). 선 스캔:
    `{"line": [128]}`. 빈 자리는 None.
    """
    out = {}
    chans = features.get("channels") or {}
    for key in (keys if keys is not None else chans.keys()):
        ch = chans.get(key) or {}
        prof = profiles(ch.get("grid"))
        if prof:
            out[key] = {"x": _znone(prof["x"]), "z": _znone(prof["z"])}
            if grid:
                out[key]["grid"] = _znone(ch["grid"])
        elif ch.get("path"):
            out[key] = {"line": _znone(ch["path"])}
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
    target = prepare(target)
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
        "cases": similar_cases(scored, tree),
    }


def similar_cases(scored: list[tuple[dict, Mapping]], tree: Tree) -> list[dict]:
    """유사 사례 — 비슷한 기록들의 라벨이 가리키는 사례(acs_case)를 사례 단위로 모은다 (SL-CASE-1).

    한 기록은 붙은 라벨마다 `tree.case_for` 의 사례 하나에 표를 던진다. 사례 점수 = 그 사례로 온
    기록의 최고 점수, `count` = 그중 `EVIDENCE_MIN` 이상인 기록 수. `EVIDENCE_MIN` 아래의 기록만
    가리키는 사례는 싣지 않는다 — 닮지 않은 기록의 사례는 근거가 아니다.
    """
    cases: dict[Any, dict] = {}
    for sim, c in scored:
        if sim["score"] < EVIDENCE_MIN:
            continue
        for lid in c["label_ids"]:
            if int(lid) not in tree:
                continue
            cid = tree.case_for(int(lid))
            if cid is None:
                continue
            e = cases.setdefault(cid, {"case_id": cid, "score": -1.0, "count": 0, "paths": [], "_seen": set()})
            e["score"] = max(e["score"], sim["score"])
            key = id(c)
            if key not in e["_seen"]:
                e["_seen"].add(key)
                e["count"] += 1
            path = tree.path(int(lid))
            if path not in e["paths"]:
                e["paths"].append(path)
    out = sorted(cases.values(), key=lambda e: (-e["score"], -e["count"], str(e["case_id"])))
    for e in out:
        del e["_seen"]
    return out[:CASE_TOP]


# ─── 묶음 (클러스터) ─────────────────────────────────────────────────────────

def group_key(scan_enum: Any, sensor_id: Any) -> str:
    """묶음 범위 하나의 이름 — (스캔 종류, 센서). 지도의 투영식도 이 키로 남는다 (SL-CLU-3 · SL-MAP-1)."""
    return f"{scan_enum or ''}|{sensor_id if sensor_id is not None else ''}"


def cluster(items: list[Mapping]) -> dict:
    """라벨과 무관하게 본 기록들을 모양으로 묶는다 (SL-CLU-2).

    items 의 각 항목: `id` · `features` · `scan_enum` · `sensor_id`(없으면 ''). 호출자가 범위마다
    `CLUSTER_MAX_N` 개로 잘라서 넣는다. **(스캔 종류, 센서)** 마다 따로 묶는다 — 경로가 다르면 1D 비교가
    무의미하고(SL-SIM-2), 센서가 다르면 신호의 기준이 달라 모양의 차이가 원인의 차이가 아니다(SL-CLU-3).

    방법(대표 기록 묶음): 모든 쌍을 비교해 `CLUSTER_MIN` 이상인 이웃을 센다. 남은 기록 중 이웃이
    가장 많은 것을 **대표**로 고르고, 그 대표와 `CLUSTER_MIN` 이상인 남은 기록을 한 묶음으로 뗀다.
    묶음이 `CLUSTER_MIN_SIZE` 보다 작아지면 멈추고 나머지는 「묶이지 않음」이다. 구성원 모두가
    **대표와 직접** 닮았으므로 한 묶음이 사슬처럼 늘어나 서로 다른 모양을 잇지 않는다.
    결과는 결정적이다(동점은 이웃 점수 합, 그다음 id).
    """
    groups: dict[tuple[str, str], list[Mapping]] = {}
    for it in items:
        sensor = it.get("sensor_id")
        groups.setdefault((str(it.get("scan_enum") or ""), "" if sensor is None else str(sensor)), []).append(it)

    clusters: list[dict] = []
    unclustered: list = []
    for scan_enum, sensor_id in sorted(groups):
        group = groups[(scan_enum, sensor_id)]
        ids = [g["id"] for g in group]
        prep = [prepare(g["features"]) for g in group]
        n = len(group)
        nb: list[dict[int, float]] = [{} for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = similarity(prep[i], prep[j])
                s = sim["score"] if sim else None
                if s is not None and s >= CLUSTER_MIN:
                    nb[i][j] = s
                    nb[j][i] = s
        left = set(range(n))
        while left:
            def density(i: int) -> tuple:
                near = nb[i].keys() & left
                return (len(near), sum(nb[i][j] for j in near), -_order_key(ids[i]))
            rep = max(left, key=density)
            near = nb[rep].keys() & left
            if len(near) + 1 < CLUSTER_MIN_SIZE:
                break
            members = [{"id": ids[rep], "score": 1.0, "representative": True}]
            members += sorted(({"id": ids[j], "score": nb[rep][j], "representative": False} for j in near),
                              key=lambda m: (-m["score"], _order_key(m["id"])))
            clusters.append({"scan_enum": scan_enum, "sensor_id": sensor_id, "representative": ids[rep],
                             "members": members})
            left -= near | {rep}
        unclustered += [ids[i] for i in sorted(left)]

    clusters.sort(key=lambda c: (-len(c["members"]), c["scan_enum"], c["sensor_id"], _order_key(c["representative"])))
    return {"clusters": clusters, "unclustered": unclustered}


# ─── 묶음 지도 (2D) ──────────────────────────────────────────────────────────

def _dist(a: Mapping, b: Mapping) -> float:
    """지도의 거리 = 1 − 점수 (0 같음 … 2 반대). 비교할 것이 없으면 1(상관없음)."""
    sim = similarity(a, b)
    s = sim["score"] if sim else None
    return 1.0 - s if s is not None else 1.0


def _top2(b: list[list[float]]) -> list[tuple[float, list[float]]]:
    """대칭 행렬의 큰 고유쌍 둘 — 거듭제곱법 + 수축. k×k(k ≤ 24)라 표준 라이브러리로 충분하다."""
    k = len(b)
    m = [row[:] for row in b]
    out: list[tuple[float, list[float]]] = []
    for e in range(2):
        v = [1.0 / math.sqrt(k) * (1 + 0.01 * ((i * 7 + e * 3) % 5)) for i in range(k)]
        lam = 0.0
        for _ in range(500):
            w = [sum(m[i][j] * v[j] for j in range(k)) for i in range(k)]
            norm = math.sqrt(sum(x * x for x in w))
            if norm < 1e-12:
                lam, v = 0.0, v
                break
            nv = [x / norm for x in w]
            lam = sum(nv[i] * sum(m[i][j] * nv[j] for j in range(k)) for i in range(k))
            if max(abs(a - c) for a, c in zip(nv, v)) < 1e-10:
                v = nv
                break
            v = nv
        out.append((lam, v))
        for i in range(k):
            for j in range(k):
                m[i][j] -= lam * v[i] * v[j]
    return out


def cluster_map(items: list[Mapping]) -> dict:
    """묶음 범위(스캔 종류 · 센서) 하나의 기록들을 2D 에 놓는다 — 가까울수록 닮았다 (SL-MAP-1).

    방법(landmark MDS): 서로 가장 먼 기록을 차례로 `MAP_LANDMARKS` 개 고르고(maxmin), 그 기준점끼리의
    거리로 고전 MDS 를 풀어 2D 자리를 정한 뒤, 나머지 기록은 **기준점까지의 거리만으로** 같은 공간에
    투영한다. 같은 투영식(`proj`)으로 이 결과에 없던 기록(새로 본 기록)도 그 자리에 놓을 수 있다.
    근사다 — 거리의 순서는 대체로 지키지만 정확한 거리는 아니다.

    반환: {"points": {id: (x, y)}, "proj": {"landmarks": [id…], "pinv": [[…], […]], "mean": […]}}
    """
    n = len(items)
    if n < 3:
        return {"points": {it["id"]: (0.0, 0.0) for it in items}, "proj": None}
    prep = [prepare(it["features"]) for it in items]
    k = min(MAP_LANDMARKS, n)
    # maxmin — 첫 기준점은 첫 기록(호출자가 id 순으로 넣는다 → 결정적).
    lms = [0]
    to_lm: list[list[float]] = [[_dist(prep[i], prep[0])] for i in range(n)]
    nearest = [row[0] for row in to_lm]
    while len(lms) < k:
        nxt = max((i for i in range(n) if i not in lms), key=lambda i: (nearest[i], -i))
        lms.append(nxt)
        for i in range(n):
            d = _dist(prep[i], prep[nxt]) if i != nxt else 0.0
            to_lm[i].append(d)
            nearest[i] = min(nearest[i], d)
    d2 = [[to_lm[lms[a]][b] ** 2 for b in range(k)] for a in range(k)]
    mean = [sum(d2[a][b] for a in range(k)) / k for b in range(k)]             # 열 평균
    grand = sum(mean) / k
    row_mean = [sum(d2[a]) / k for a in range(k)]
    bmat = [[-0.5 * (d2[a][b] - row_mean[a] - mean[b] + grand) for b in range(k)] for a in range(k)]
    pinv = []
    for lam, v in _top2(bmat):
        pinv.append([x / math.sqrt(lam) for x in v] if lam > 1e-12 else [0.0] * k)
    proj = {"landmarks": [items[i]["id"] for i in lms], "pinv": pinv, "mean": mean}
    points = {items[i]["id"]: project(proj, to_lm[i]) for i in range(n)}
    return {"points": points, "proj": proj}


def project(proj: Mapping, dists: list[float]) -> tuple[float, float]:
    """기준점까지의 거리 → 지도의 자리. x = ½ · L⁺ · (mean − δ²)."""
    delta = [m - d * d for m, d in zip(proj["mean"], dists)]
    x, y = (0.5 * sum(p * q for p, q in zip(row, delta)) for row in proj["pinv"])
    return (round(x, 5), round(y, 5))


def project_features(proj: Mapping, features: Mapping, landmark_features: list[Mapping]) -> tuple[float, float]:
    """결과에 없던 기록 하나를 같은 지도에 놓는다 — 기준점과 `MAP_LANDMARKS` 번만 비교한다."""
    p = prepare(features)
    return project(proj, [_dist(p, prepare(lf)) for lf in landmark_features])


def _order_key(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def cluster_category(member_labels: list[list[int]], tree: Tree) -> dict:
    """한 묶음의 분류 — 라벨된 구성원들의 라벨을 단계별로 센다 (SL-CLU-4).

    `member_labels` = 구성원마다 붙은 라벨 id(라벨 없으면 빈 목록). 라벨은 조상까지 센다(SL-SUG-1).
    단계 L 의 노드 몫 = 그 노드를 가진 라벨된 구성원 / 라벨된 구성원 전체. 단계 L 이 **합의**되려면
    1위 몫이 `CLUSTER_AGREE` 이상이고, 2위보다 많고, (L>1 이면) 바로 위 단계의 합의 노드의 자식이어야
    한다. `category` = 합의된 가장 깊은 단계(없으면 None) — 라벨 안 된 구성원에게 붙일 후보다.
    """
    labelled = [[int(i) for i in ids if int(i) in tree] for ids in member_labels]
    labelled = [ids for ids in labelled if ids]
    n = len(labelled)
    counts: dict[int, int] = {}
    for ids in labelled:
        for node in tree.closure(ids):
            counts[node] = counts.get(node, 0) + 1
    levels: dict[int, list[dict]] = {}
    chosen: Optional[int] = None
    category = None
    for level in range(1, MAX_LEVEL + 1):
        row = sorted(((i, c) for i, c in counts.items() if int(tree.get(i)["level"]) == level),
                     key=lambda t: (-t[1], t[0]))
        agreed = False
        if row and n and (level == 1 or chosen is not None):
            top_id, top = row[0]
            parent = tree.get(top_id)["parent_id"]
            agreed = (top / n >= CLUSTER_AGREE
                      and (len(row) < 2 or top > row[1][1])
                      and (level == 1 or (parent is not None and int(parent) == chosen)))
        chosen = row[0][0] if agreed else None
        levels[level] = [
            {"label_id": i, "path": tree.path(i), "count": c, "share": c / n,
             "case_id": tree.case_for(i), "agreed": agreed and j == 0}
            for j, (i, c) in enumerate(row[:LEVEL_TOP])
        ]
        if agreed:
            category = {**levels[level][0], "level": level}
    return {"labelled": n, "unlabelled": len(member_labels) - n, "levels": levels, "category": category}


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
