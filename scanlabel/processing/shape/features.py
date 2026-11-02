"""값 읽기 · 스냅샷 — 스캔 한 건에서 채널별 측정 순서 곡선 · 칸을 뽑는다."""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from .constants import FEATURE_VERSION, GRID_N, PATH_N


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
