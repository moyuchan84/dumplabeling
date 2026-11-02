"""채널 간 편차(skew) — 묶음 · 지도의 첫 번째 잣대."""
from __future__ import annotations

import statistics
from typing import Iterable, Mapping, Optional

from .constants import CLUSTER_MIN_SIZE, SKEW_FLOOR, W_1D, W_2D, W_SEQ
from .similarity import _SEQ_MIN, _dot, _flat, _mean, profiles


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
