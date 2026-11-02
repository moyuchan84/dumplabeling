"""묶음 지도(2D) — 기준점 투영."""
from __future__ import annotations

import math
from typing import Mapping, Optional

from .constants import MAP_LANDMARKS
from .pca import pca_coords, pca_model
from .skew import prepare_skew, skew_baseline, skew_similarity
from .clustering import check_method


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
