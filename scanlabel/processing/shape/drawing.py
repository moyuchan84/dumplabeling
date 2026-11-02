"""그림(SL-DRAW) — 평균 곡선 · 얇게 줄인 곡선."""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from .constants import GRID_N, PATH_N


DRAW_THIN = 64           # 목록 · 지도에 싣는 평균 곡선의 점 수 — 상세는 PATH_N 그대로


def draw_shape(features: Mapping) -> dict:
    """그림용 한 기록 — 채널마다 **측정 순서 곡선**(`path`, PATH_N 점)과 **칸**(`grid`, 행 = Z 아래부터 · 열 = X).

    poc_viewer 대시보드의 두 그림(「경로 겹쳐 보기」 = 측정 순서대로 본 값, 「실공간 맵」 = X–Z 평면의 값)을 스냅샷에서
    그대로 줄인 것이다. 값은 **그 채널 점 전체의 평균 · 표준편차로 정규화**(SL-CLU-9 의 정규화와 같다) — 기록마다
    크기와 바탕이 달라 원래 값으로는 평균을 낼 수 없고, 채널끼리 한 자에 겹쳐 그리면 어디서 갈라지는지가 보인다.
    평평한 채널은 0 선. 빈 자리는 None.
    """
    out: dict[str, dict] = {}
    for key in sorted((features.get("channels") or {})):
        ch = features["channels"][key] or {}
        st = ch.get("stats") or {}
        mu, sd = st.get("mean"), st.get("std")
        if mu is None:
            continue

        def f(v: Optional[float], mu: float = mu, sd: Optional[float] = sd) -> Optional[float]:
            if v is None:
                return None
            return round((v - mu) / sd, 3) if sd and sd > 0 else 0.0
        path = [f(v) for v in ch.get("path") or []]
        grid = [f(v) for v in ch["grid"]] if ch.get("grid") else None
        if path or grid:
            out[key] = {"path": path or None, "grid": grid}
    return out


def _quantile(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def mean_shape(features_list: Iterable[Mapping]) -> dict:
    """묶음의 평균 그림 — 구성원마다 `draw_shape` 한 것을 자리마다 평균 (SL-DRAW-2).

    채널마다 `path` = 평균 곡선, `lo` · `hi` = 그 자리의 25 · 75 백분위(구성원 절반이 드는 띠 — 묶음이 얼마나
    고른가), `grid` = 칸마다 평균. 구성원 절반 이상에 있는 채널만. `n` = 구성원 수.
    """
    shapes = [draw_shape(f) for f in features_list]
    n = len(shapes)
    counts: dict[str, int] = {}
    for s in shapes:
        for k in s:
            counts[k] = counts.get(k, 0) + 1
    channels: dict[str, dict] = {}
    for key in sorted(k for k, c in counts.items() if 2 * c >= n):
        paths = [s[key]["path"] for s in shapes if key in s and s[key]["path"] and len(s[key]["path"]) == PATH_N]
        grids = [s[key]["grid"] for s in shapes if key in s and s[key]["grid"]]
        mean_p: Optional[list] = None
        lo: Optional[list] = None
        hi: Optional[list] = None
        if paths:
            mean_p, lo, hi = [], [], []
            for j in range(PATH_N):
                vals = sorted(p[j] for p in paths if p[j] is not None)
                if not vals:
                    mean_p.append(None), lo.append(None), hi.append(None)
                    continue
                mean_p.append(round(sum(vals) / len(vals), 3))
                lo.append(round(_quantile(vals, 0.25), 3))
                hi.append(round(_quantile(vals, 0.75), 3))
        mean_g: Optional[list] = None
        if grids:
            mean_g = []
            for j in range(GRID_N * GRID_N):
                vals = [g[j] for g in grids if g[j] is not None]
                mean_g.append(round(sum(vals) / len(vals), 3) if vals else None)
        if mean_p or mean_g:
            channels[key] = {"path": mean_p, "lo": lo, "hi": hi, "grid": mean_g}
    return {"n": n, "channels": channels}


def thin_shape(shape_: Optional[Mapping], n: int = DRAW_THIN) -> Optional[dict]:
    """목록 · 지도용 — 평균 곡선만 `n` 점으로(구간 평균), 띠 · 칸은 뺀다."""
    if not shape_:
        return None
    out = {}
    for key, ch in (shape_.get("channels") or {}).items():
        p = ch.get("path")
        if not p:
            continue
        k = len(p) / n
        thin = []
        for j in range(n):
            seg = [v for v in p[int(j * k):max(int((j + 1) * k), int(j * k) + 1)] if v is not None]
            thin.append(round(sum(seg) / len(seg), 3) if seg else None)
        out[key] = {"path": thin}
    return {"n": shape_.get("n"), "channels": out}
