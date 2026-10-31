"""모양 특징과 유사도 — 순수 함수, 표준 라이브러리만 (SPEC.md 「모양 특징」·「유사도」·「제안」).

기록 한 건 = dump raw 스캔 하나(`value{채널: [..]}` + `xyz{x,z}`). 채널마다

- **측정 순서 곡선**: 스캔 순서 배열을 `PATH_N` 점으로 줄인 것(구간 평균; 짧으면 선형 보간).
- **칸**: (x, z) 를 0–1 평면에 놓고 `GRID_N`×`GRID_N` 칸 평균(빈 칸은 None).

을 **정규화 전** 값으로 스냅샷에 남긴다. 비교 재료 고르기 · 정규화(z-score) · 비교는 `similarity` 가
그때그때 한다 — 그 방식을 바꾸는 변경은 원본 없이 스냅샷만으로 다시 계산된다.

- 면 스캔(칸이 있다): 1D = **X 단면 · Z 단면**(칸의 열 · 행 평균), 2D = 칸. 셋을 **각자** 정규화한다.
  측정 순서 곡선은 쓰지 않는다 — 면을 한 줄로 펴면 행이 꺾이는 자리가 섞여 모양이 뭉개진다.
- 선 스캔(칸이 없다): 1D = 측정 순서 곡선.

묶음 · 지도는 모양 대신 **채널 간 편차(skew)**로 잰다 — 같은 자리(칸 · 단면 칸 · 경로 칸)에서 채널마다
(제 정규화 값 − 채널 평균). dump 오류는 한 채널의 모양보다 채널(tr · txh · tyh …)끼리 어긋나는 자리와 방향으로
갈린다(SL-CLU-9). 면 스캔의 편차에는 **측정 순서 곡선도** 넣는다(SL-CLU-11) — 채널은 `xyz` 를 함께 쓰므로 같은
인덱스는 같은 점이고, 경로 칸 하나에 행이 꺾이는 자리가 섞여도 모든 채널에 똑같이 섞여 채널끼리의 어긋남은
남는다. 라벨 제안(`rank`)은 그대로 모양 유사도다(모양에는 측정 순서 곡선을 쓰지 않는다, SL-SIM-1).

문턱값 · 가중치는 **휴리스틱**이다. `evaluate`(leave-one-out 적중률)로 재고 여기서만 고친다.
"""
from __future__ import annotations

import math
import statistics
import operator
from typing import Any, Iterable, Mapping, Optional

from .tree import MAX_LEVEL, Tree

FEATURE_VERSION = 1      # 스냅샷의 모양(해상도 · 필드)이 바뀌면 올린다 → jobs refeature
PATH_N = 128
GRID_N = 16

W_1D = 0.5               # 채널 점수 = 가중 평균(있는 쪽만)
W_2D = 0.5
W_SEQ = 0.5              # 면 스캔 편차의 측정 순서 곡선 몫(SL-CLU-11) — 단면 · 칸 · 측정 순서가 1/3 씩
MIN_OVERLAP = 16         # 2D 상관을 낼 최소 공통 칸 수
PROFILE_MIN = 8          # 단면(X · Z) 상관을 낼 최소 공통 자리 수 (단면 길이 = GRID_N)
FLAT_REL = 1e-6          # 범위가 크기의 이 비율 이하면 평평한 신호

SUGGEST_MIN = 0.80       # 이 점수 이상이어야 「제안」
MARGIN = 0.05            # 같은 단계의 2위보다 이만큼 앞서야 「확실」(아니면 갈림)
EVIDENCE_MIN = 0.60      # 근거 건수에 넣는 최소 점수
LEVEL_TOP = 5            # 단계별 순위에 싣는 노드 수
CASE_TOP = 5             # 유사 사례 목록에 싣는 사례 수

CLUSTER_MIN = 0.80       # 묶음의 대표 기록과 편차 점수가 이 이상이어야 그 묶음의 구성원
SKEW_FLOOR = 0.10        # 채널 간 편차의 바닥(각 채널 표준편차 단위, RMS) — 이보다 작은 어긋남은 잡음으로 본다
CLUSTER_MIN_SIZE = 3     # 이보다 작은 무리는 묶음이 아니다(「묶이지 않음」)
CLUSTER_MAX_N = 800      # 묶음 범위(스캔 종류 · 척 · 센서) 하나에서 묶는 최대 기록 수(최근 것부터) — 쌍 비교가 n² 이다
CLUSTER_AGREE = 0.60     # 라벨된 구성원 중 이 비율 이상이 같은 노드여야 그 묶음의 분류
MAP_LANDMARKS = 24       # 묶음 지도의 기준점 수 — 기록마다 이만큼만 비교해 2D 자리를 정한다

METHODS = ("skew", "pca")  # 묶음 · 지도의 잣대 — 둘 다 저장하고 화면이 고른다 (SL-CLU-10)
PCA_W = {"shape": 0.4, "skew": 0.4, "amp": 0.2}     # 주성분 재료의 몫 — 채널별 차이 · 채널 간 어긋남 · 크기
PCA_MAT_W = {"area": {"x": 0.2, "z": 0.2, "grid": 0.3, "path": 0.3}, "line": {"path": 1.0}}
PCA_GRID = 8             # 칸은 8×8 로 줄여 넣는다 — 피처 수를 줄이고 이웃 칸의 잡음을 평균한다
PCA_PATH = 64            # 측정 순서 곡선도 반으로
PCA_MAX_K = 10           # 남길 주성분의 최대 수
PCA_VAR = 0.90           # 이만큼의 분산을 설명할 때까지 주성분을 남긴다
PCA_FLOOR = 0.10         # 「평소」에서 이보다 덜 벗어난 차이는 잡음(값 하나당 RMS, 표준편차 · log 크기 단위)
PCA_ITER = 300           # 주성분 반복의 상한 — 보통 수십 번에 수렴한다


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


# ─── 채널 간 편차 (skew) — 묶음 · 지도의 잣대 ──────────────────────────────────

def _skew_norm(features: Mapping) -> tuple[Optional[str], dict[str, dict[str, list]]]:
    """채널마다 비교 재료를 **그 채널의 점 통계(mean · std)**로 정규화한다 — 단위가 다른 채널을 한 자로.

    면 스캔: 칸 · X 단면 · Z 단면 · 측정 순서 곡선(SL-CLU-11), 선 스캔: 측정 순서 곡선. 칸 평균은 점 값의
    선형 평균이라 점마다 정규화한 뒤 칸에서 평균한 것과 같다 — 원본 없이 스냅샷만으로 계산된다. 평평한 채널은 0 선.
    """
    chans = features.get("channels") or {}
    area = {k: ch for k, ch in chans.items() if (ch or {}).get("grid")}
    kind = "area" if area else "line"
    use = area or {k: ch for k, ch in chans.items() if (ch or {}).get("path")}
    out: dict[str, dict[str, list]] = {}
    for key, ch in use.items():
        st = ch.get("stats") or {}
        mean, std = st.get("mean"), st.get("std")
        flat = mean is None or not std or _flat([st.get("min") or 0.0, st.get("max") or 0.0])

        def z(vals: list) -> list[Optional[float]]:
            return [None if v is None else (0.0 if flat else (v - mean) / std) for v in vals]
        if kind == "area":
            g = z(ch["grid"])
            out[str(key)] = {"grid": g, **(profiles(g) or {}), "path": z(ch.get("path") or [])}
        else:
            out[str(key)] = {"path": z(ch["path"])}
    return (kind if out else None), out


_SKEW_MATS = {"area": ("x", "z", "grid", "path"), "line": ("path",)}


def _base_fits(baseline: Optional[Mapping], kind: Optional[str], keys: list[str]) -> bool:
    """기준선이 이 기록의 종류 · 채널 집합 · 재료와 맞나. SL-CLU-11 전에 저장된 면 스캔 기준선에는
    측정 순서 곡선이 없다 — 그 기준선은 쓰지 않는다(다시 묶으면 새것이 저장된다)."""
    return bool(baseline and kind and baseline["kind"] == kind and baseline["keys"] == keys
                and all(m in baseline["mats"] for m in _SKEW_MATS[kind]))


def _skew_dev(norm: Mapping, keys: list[str], mat: str) -> list[Optional[list[float]]]:
    """자리마다 채널들의 (값 − 채널 평균). 한 채널이라도 비면 그 자리는 None — 같은 채널 집합끼리만 잰다."""
    seqs = [norm[k][mat] for k in keys]
    out: list[Optional[list[float]]] = []
    for j in range(min(len(s) for s in seqs)):
        vals = [s[j] for s in seqs]
        if any(v is None for v in vals):
            out.append(None)
            continue
        m = sum(vals) / len(vals)
        out.append([v - m for v in vals])
    return out


def _skew_pack(dev: list[Optional[list[float]]], nch: int) -> tuple[list[float], list[float], list[float]]:
    """편차를 한 줄로 — (값, 값², 있음 표시). 빈 자리는 0 이라 내적이 공통 자리만 센다(`_dot` 하나로)."""
    v: list[float] = []
    for d in dev:
        v += d if d is not None else [0.0] * nch
    mask = [0.0 if d is None else 1.0 for d in dev for _ in range(nch)]
    return v, [x * x for x in v], mask


def skew_baseline(features_list: Iterable[Mapping]) -> Optional[dict]:
    """묶음 범위의 「평소 어긋남」 — 자리마다 채널 편차의 **중앙값** (SL-CLU-9).

    채널끼리 원래 다르게 움직이면(한 채널이 늘 반대 · 늘 앞섬) 그 고정된 편차가 모든 기록에 똑같이 실려
    오류의 어긋남을 덮는다. 범위 안 기록들의 중앙값을 빼면 「이 기록만의 어긋남」이 남는다. 중앙값이라
    한 묶음이 범위의 대부분을 차지해도 그 묶음의 어긋남까지 지우지는 않는다(과반 미만이면).
    가장 흔한 (종류, 채널 집합)의 기록으로만 낸다. 기록이 `CLUSTER_MIN_SIZE` 보다 적으면 None.
    """
    preps = [prepare_skew(f) for f in features_list]
    sets = [(p["kind"], tuple(p["keys"])) for p in preps if p["full"]]
    if not sets:
        return None
    (kind, keys), _ = max(((s, sets.count(s)) for s in set(sets)), key=lambda t: (t[1], t[0]))
    same = [p for p in preps if p["full"] and (p["kind"], tuple(p["keys"])) == (kind, keys)]
    if len(same) < CLUSTER_MIN_SIZE:
        return None
    mats = {}
    for m in _SKEW_MATS[kind]:
        col = []
        for j in range(len(same[0]["full"][m][0])):
            have = [p["full"][m][0][j] for p in same if p["full"][m][2][j]]
            col.append(round(statistics.median(have), 6) if have else 0.0)
        mats[m] = col
    return {"kind": kind, "keys": list(keys), "mats": mats}


def prepare_skew(features: Mapping, baseline: Optional[Mapping] = None) -> dict:
    """편차 비교 준비 — 채널 정규화와 (제 채널 전부로 낸) 편차를 한 번만. 묶음의 n² 비교에서 쓴다.

    `baseline`(같은 종류 · 같은 채널 집합의 `skew_baseline`)을 주면 편차에서 그것을 뺀다.
    """
    if features.get("_skew"):
        return dict(features)
    kind, norm = _skew_norm(features)
    keys = sorted(norm)
    full = {}
    centred = False
    if kind and len(keys) >= 2:
        full = {m: _skew_pack(_skew_dev(norm, keys, m), len(keys)) for m in _SKEW_MATS[kind]}
        if _base_fits(baseline, kind, keys):
            centred = True
            for m, (v, _, mask) in full.items():
                v = [x - b * k for x, b, k in zip(v, baseline["mats"][m], mask)]
                full[m] = (v, [x * x for x in v], mask)
    return {"_skew": True, "kind": kind, "norm": norm, "keys": keys, "full": full, "centred": centred}


def _skew_score(pa: tuple, pb: tuple, nch: int, min_n: int) -> Optional[float]:
    """두 편차 장의 닮음 = (2·u·v + f) / (|u|² + |v|² + f), f = 2·n·SKEW_FLOOR² (n = 공통 값 수).

    자리 · 방향 · 크기가 모두 같아야 1 이다 — 상관과 달리 크기를 다시 지우지 않는다(채널마다 이미 제
    표준편차로 쟀다). 둘 다 바닥보다 작으면(채널끼리 어긋나지 않음) 1 쪽, 한쪽만 크면 0 쪽, 반대면 −1 쪽.
    """
    ua, ua2, ma = pa
    ub, ub2, mb = pb
    n = _dot(ma, mb)
    if n / nch < min_n:
        return None
    f = 2.0 * n * SKEW_FLOOR * SKEW_FLOOR
    return max(-1.0, min(1.0, (2.0 * _dot(ua, ub) + f) / (_dot(ua2, mb) + _dot(ub2, ma) + f)))


def skew_similarity(a: Mapping, b: Mapping) -> Optional[dict]:
    """채널 간 편차의 닮음 — 묶음 · 지도의 잣대 (SL-CLU-9). 스냅샷이나 `prepare_skew` 한 것을 받는다.

    공통 채널이 둘 이상이고 같은 종류(면 · 선)여야 잰다. 면: 1D = X · Z 단면 점수의 평균, 2D = 칸,
    측정 순서 = 경로 칸(`dp`), 점수 = `W_1D`·1D + `W_2D`·2D + `W_SEQ`·측정 순서(있는 쪽만, SL-CLU-11).
    선: 측정 순서 곡선 하나. 두 기록이 같은 채널 집합이고 같은
    기준선을 뺐을 때만 준비한 편차를 쓴다 — 아니면 공통 채널만으로 편차를 다시 내고 기준선은 빼지 않는다
    (채널 평균이 채널 집합에 따라 달라지고, 기준선은 한 채널 집합의 것이다).
    """
    pa, pb = prepare_skew(a), prepare_skew(b)
    if pa["kind"] is None or pa["kind"] != pb["kind"]:
        return None
    keys = sorted(set(pa["keys"]) & set(pb["keys"]))
    if len(keys) < 2:
        return None
    ready = pa["keys"] == keys == pb["keys"] and pa["centred"] == pb["centred"]

    def packed(p: dict, m: str) -> tuple:
        if ready:
            return p["full"][m]
        return _skew_pack(_skew_dev(p["norm"], keys, m), len(keys))
    s = {m: _skew_score(packed(pa, m), packed(pb, m), len(keys), _SEQ_MIN[m]) for m in _SKEW_MATS[pa["kind"]]}
    if pa["kind"] == "area":
        s1, s2, sp = _mean((s["x"], s["z"])), s["grid"], s["path"]
    else:
        s1, s2, sp = s["path"], None, None
    have = [(w, v) for w, v in ((W_1D, s1), (W_2D, s2), (W_SEQ, sp)) if v is not None]
    if not have:
        return None
    return {"score": sum(w * v for w, v in have) / sum(w for w, _ in have), "d1": s1, "d2": s2,
            "dx": s.get("x"), "dz": s.get("z"), "dp": sp if pa["kind"] == "area" else None, "channels": keys}


def skew_profiles(features: Mapping, baseline: Optional[Mapping] = None, *, grid: bool = False) -> dict:
    """겹쳐 그리기용 채널 간 편차 — 면: 채널마다 `{"x", "z", "line"}`(+ `grid=True` 면 `"grid"`; `line` =
    측정 순서 곡선, SL-CLU-11), 선: `{"line"}`.

    값은 각 채널 표준편차 단위의 「채널 평균에서 벗어난 정도」라 채널끼리 한 자로 그린다. `baseline` 이
    이 기록의 종류 · 채널 집합과 맞으면 그것을 뺀 값(묶음이 본 그대로). 소수 셋째 자리, 빈 자리는 None.
    채널이 하나뿐이면 빈 dict. **이 기록 하나(와 범위의 기준선)만으로** 정해진다 — 비교 상대와 무관하다.
    """
    kind, norm = _skew_norm(features)
    keys = sorted(norm)
    if not kind or len(keys) < 2:
        return {}
    base = baseline if _base_fits(baseline, kind, keys) else None
    mats = ("path",) if kind == "line" else ("x", "z", "path", "grid") if grid else ("x", "z", "path")
    out: dict[str, dict] = {k: {} for k in keys}
    nch = len(keys)
    for m in mats:
        dev = _skew_dev(norm, keys, m)
        for i, k in enumerate(keys):
            out[k]["line" if m == "path" else m] = [
                None if d is None else round(d[i] - (base["mats"][m][j * nch + i] if base else 0.0), 3)
                for j, d in enumerate(dev)]
    return out


# ─── 주성분 (PCA) — 묶음 · 지도의 두 번째 잣대 (SL-CLU-10) ───────────────────
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

def _txt(v: Any) -> str:
    return "" if v is None else str(v)


def group_of(it: Mapping) -> tuple[str, str, str]:
    """묶음 범위 — (스캔 종류, 척, 센서). 없는 값은 ''."""
    return (_txt(it.get("scan_enum")), _txt(it.get("chuck_id")), _txt(it.get("sensor_id")))


def group_key(scan_enum: Any, chuck_id: Any, sensor_id: Any) -> str:
    """묶음 범위 하나의 이름 — (스캔 종류, 척, 센서). 지도의 투영식도 이 키로 남는다 (SL-CLU-3 · SL-MAP-1)."""
    return f"{_txt(scan_enum)}|{_txt(chuck_id)}|{_txt(sensor_id)}"


def _scorer(group: list[Mapping], method: str, model: Optional[Mapping]):
    """묶음 범위 하나의 쌍 점수 — `skew`: 채널 간 편차(기준선을 빼고), `pca`: 주성분 좌표."""
    if method == "pca":
        coords = [pca_coords(model, g["features"]) if model else None for g in group]
        return lambda i, j: pca_similarity(coords[i], coords[j])
    base = skew_baseline(g["features"] for g in group)
    prep = [prepare_skew(g["features"], base) for g in group]

    def score(i: int, j: int) -> Optional[float]:
        sim = skew_similarity(prep[i], prep[j])
        return sim["score"] if sim else None
    return score


def check_method(method: str) -> None:
    if method not in METHODS:
        raise ValueError(f"묶음 잣대는 {', '.join(METHODS)} 중 하나: {method!r}")


def cluster(items: list[Mapping], method: str = "skew", *, models: Optional[Mapping] = None) -> dict:
    """라벨과 무관하게 본 기록들을 모양으로 묶는다 (SL-CLU-2).

    items 의 각 항목: `id` · `features` · `scan_enum` · `chuck_id` · `sensor_id`(없으면 ''). 호출자가 범위마다
    `CLUSTER_MAX_N` 개로 잘라서 넣는다. **(스캔 종류, 척, 센서)** 마다 따로 묶는다 — 경로가 다르면 1D 비교가
    무의미하고(SL-SIM-2), 척 · 센서가 다르면 신호의 기준이 달라 모양의 차이가 원인의 차이가 아니다(SL-CLU-3).

    잣대는 `method` — **채널 간 편차**(`skew`, SL-CLU-9) 또는 **주성분**(`pca`, SL-CLU-10; `models` 는
    범위 → `pca_model`, 없으면 여기서 낸다). 투영식에 놓이지 않는 기록은 「묶이지 않음」이다.
    방법(대표 기록 묶음): 모든 쌍을 비교해
    `CLUSTER_MIN` 이상인 이웃을 센다. 남은 기록 중 이웃이
    가장 많은 것을 **대표**로 고르고, 그 대표와 `CLUSTER_MIN` 이상인 남은 기록을 한 묶음으로 뗀다.
    묶음이 `CLUSTER_MIN_SIZE` 보다 작아지면 멈추고 나머지는 「묶이지 않음」이다. 구성원 모두가
    **대표와 직접** 닮았으므로 한 묶음이 사슬처럼 늘어나 서로 다른 모양을 잇지 않는다.
    결과는 결정적이다(동점은 이웃 점수 합, 그다음 id).
    """
    check_method(method)
    groups: dict[tuple[str, str, str], list[Mapping]] = {}
    for it in items:
        groups.setdefault(group_of(it), []).append(it)

    clusters: list[dict] = []
    unclustered: list = []
    for scan_enum, chuck_id, sensor_id in sorted(groups):
        group = groups[(scan_enum, chuck_id, sensor_id)]
        ids = [g["id"] for g in group]
        model = None
        if method == "pca":
            gid = (scan_enum, chuck_id, sensor_id)
            model = models[gid] if models is not None and gid in models else                 pca_model(g["features"] for g in group)
        score = _scorer(group, method, model)
        n = len(group)
        nb: list[dict[int, float]] = [{} for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                s = score(i, j)
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
            clusters.append({"scan_enum": scan_enum, "chuck_id": chuck_id, "sensor_id": sensor_id,
                             "representative": ids[rep], "members": members})
            left -= near | {rep}
        unclustered += [ids[i] for i in sorted(left)]

    clusters.sort(key=lambda c: (-len(c["members"]), c["scan_enum"], c["chuck_id"], c["sensor_id"],
                                 _order_key(c["representative"])))
    return {"clusters": clusters, "unclustered": unclustered}


# ─── 묶음 지도 (2D) ──────────────────────────────────────────────────────────

def _dist(a: Mapping, b: Mapping) -> float:
    """지도의 거리 = 1 − 편차 점수 (0 같음 … 2 반대) — 묶음과 같은 잣대. 비교할 것이 없으면 1(상관없음)."""
    sim = skew_similarity(a, b)
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


def cluster_map(items: list[Mapping], method: str = "skew", *, model: Optional[Mapping] = None) -> dict:
    """묶음 범위(스캔 종류 · 척 · 센서) 하나의 기록들을 2D 에 놓는다 — 가까울수록 채널 간 편차가 닮았다 (SL-MAP-1).

    `method="pca"`(SL-CLU-10): 자리 = 첫째 · 둘째 주성분 좌표 그대로(근사가 아니라 투영, 축에 뜻이 있다).
    투영식 = `pca_model`(+ 화면이 그릴 채널 간 편차의 기준선). 투영식에 놓이지 않는 기록은 점이 없다.

    방법(landmark MDS): 서로 가장 먼 기록을 차례로 `MAP_LANDMARKS` 개 고르고(maxmin), 그 기준점끼리의
    거리로 고전 MDS 를 풀어 2D 자리를 정한 뒤, 나머지 기록은 **기준점까지의 거리만으로** 같은 공간에
    투영한다. 같은 투영식(`proj`)으로 이 결과에 없던 기록(새로 본 기록)도 그 자리에 놓을 수 있다.
    근사다 — 거리의 순서는 대체로 지키지만 정확한 거리는 아니다.

    범위의 기준선(`skew_baseline`)을 빼고 잰다 — 묶음과 같은 잣대. 기준선도 투영식에 남긴다.

    반환: {"points": {id: (x, y)}, "proj": {"landmarks": [id…], "pinv": [[…], […]], "mean": […], "baseline": …}}
    """
    check_method(method)
    n = len(items)
    if method == "pca":
        model = model if model is not None else pca_model(it["features"] for it in items)
        if model is None:
            return {"points": {it["id"]: (0.0, 0.0) for it in items}, "proj": None}
        proj = {**model, "baseline": skew_baseline(it["features"] for it in items)}
        points = {}
        for it in items:
            c = pca_coords(model, it["features"])
            if c is not None:
                points[it["id"]] = _xy(c)
        return {"points": points, "proj": proj}
    if n < 3:
        return {"points": {it["id"]: (0.0, 0.0) for it in items}, "proj": None}
    base = skew_baseline(it["features"] for it in items)
    prep = [prepare_skew(it["features"], base) for it in items]
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
    proj = {"landmarks": [items[i]["id"] for i in lms], "pinv": pinv, "mean": mean, "baseline": base}
    points = {items[i]["id"]: project(proj, to_lm[i]) for i in range(n)}
    return {"points": points, "proj": proj}


def project(proj: Mapping, dists: list[float]) -> tuple[float, float]:
    """기준점까지의 거리 → 지도의 자리. x = ½ · L⁺ · (mean − δ²)."""
    delta = [m - d * d for m, d in zip(proj["mean"], dists)]
    x, y = (0.5 * sum(p * q for p, q in zip(row, delta)) for row in proj["pinv"])
    return (round(x, 5), round(y, 5))


def _xy(c: list[float]) -> tuple[float, float]:
    return (round(c[0], 5) if c else 0.0, round(c[1], 5) if len(c) > 1 else 0.0)


def project_features(proj: Mapping, features: Mapping,
                     landmark_features: Optional[list[Mapping]] = None) -> Optional[tuple[float, float]]:
    """결과에 없던 기록 하나를 같은 지도에 놓는다 — 기준점과 `MAP_LANDMARKS` 번만 비교한다.

    주성분 지도(`proj["method"] == "pca"`)는 기준점 없이 투영식만으로 놓는다. 놓을 수 없으면 None.
    """
    if proj.get("method") == "pca":
        c = pca_coords(proj, features)
        return None if c is None else _xy(c)
    base = proj.get("baseline")
    p = prepare_skew(features, base)
    return project(proj, [_dist(p, prepare_skew(lf, base)) for lf in landmark_features])


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


def cluster_quality(result: Mapping, labels: Mapping[Any, list[int]], tree: Tree) -> dict:
    """묶음 결과를 라벨로 잰다 — 두 잣대(SL-CLU-10)를 실데이터로 견주는 숫자.

    `labels` = 기록 id → 붙은 라벨 id. 단계 L 의 **순도** = 묶음마다 (가장 많은 노드를 가진 구성원 수)의
    합 / (그 단계까지 라벨된 묶인 구성원 수). **덮음** = 라벨된 기록 중 어느 묶음에든 든 몫. 묶음 수가
    많고 작을수록 순도는 쉽게 오른다 — 둘을 함께 본다.
    """
    ids = [m["id"] for c in result["clusters"] for m in c["members"]] + list(result["unclustered"])
    labelled = {i for i in ids if any(int(x) in tree for x in labels.get(i) or [])}
    clustered = {m["id"] for c in result["clusters"] for m in c["members"]} & labelled
    purity: dict[str, Optional[float]] = {}
    for level in range(1, MAX_LEVEL + 1):
        hit = tot = 0
        for c in result["clusters"]:
            sets = []
            for m in c["members"]:
                if m["id"] in labelled:
                    nodes = {x for x in tree.closure(int(i) for i in labels[m["id"]] if int(i) in tree)
                             if int(tree.get(x)["level"]) == level}
                    if nodes:
                        sets.append(nodes)
            if not sets:
                continue
            counts: dict[int, int] = {}
            for s in sets:
                for x in s:
                    counts[x] = counts.get(x, 0) + 1
            hit += max(counts.values())
            tot += len(sets)
        purity[str(level)] = round(hit / tot, 3) if tot else None
    return {"labelled": len(labelled), "clustered_labelled": len(clustered),
            "coverage": round(len(clustered) / len(labelled), 3) if labelled else None, "purity": purity}


def partition_agreement(a: Mapping, b: Mapping) -> Optional[float]:
    """두 묶음 결과가 얼마나 같게 갈랐나 — adjusted Rand index (1 같음, 0 우연 수준). 묶이지 않은 기록은 혼자."""
    def lab(r: Mapping) -> dict:
        out = {m["id"]: f"c{n}" for n, c in enumerate(r["clusters"]) for m in c["members"]}
        out.update({i: f"u{i}" for i in r["unclustered"]})
        return out
    la, lb = lab(a), lab(b)
    ids = sorted(la.keys() & lb.keys(), key=str)
    n = len(ids)
    if n < 2:
        return None
    pair = lambda x: x * (x - 1) / 2
    cells: dict[tuple, int] = {}
    ra: dict[str, int] = {}
    rb: dict[str, int] = {}
    for i in ids:
        cells[(la[i], lb[i])] = cells.get((la[i], lb[i]), 0) + 1
        ra[la[i]] = ra.get(la[i], 0) + 1
        rb[lb[i]] = rb.get(lb[i], 0) + 1
    index = sum(pair(v) for v in cells.values())
    sa, sb = sum(pair(v) for v in ra.values()), sum(pair(v) for v in rb.values())
    expected = sa * sb / pair(n)
    top = (sa + sb) / 2
    return 1.0 if top == expected else round((index - expected) / (top - expected), 3)


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
