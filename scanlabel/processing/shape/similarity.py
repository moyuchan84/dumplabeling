"""모양 유사도 — 채널별 상관 · 비교 재료(prepare)."""
from __future__ import annotations

import math
import operator
from typing import Any, Iterable, Mapping, Optional

from .constants import FLAT_REL, GRID_N, MIN_OVERLAP, PROFILE_MIN, W_1D, W_2D


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
