"""읽는 법의 실제 예시 — 데이터가 묶음이 되기까지의 중간값 (SL-DOC-2)."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .clustering import _cut, _linkage, _order_key, _silhouette, best_cut
from .pca import _model_mats, _pca_raw, _pca_resid, pca_axes, pca_coords, pca_model, pca_parts, pca_similarity
from .constants import CLUSTER_K_MAX, METHODS
from .skew import _skew_norm, prepare_skew, skew_baseline, skew_profiles, skew_similarity


WALK_SAMPLE = 36         # 예시에 싣는 기록 수 — 거리표 · 덴드로그램을 한 화면에 그릴 수 있는 크기
WALK_CLUSTERS = 4        # 표본을 고르는 실제 판의 묶음 수(큰 것부터) — 나머지 자리는 묶이지 않은 기록


def walk_sample(ids: list, run_no: Mapping[Any, Optional[int]], n: int = WALK_SAMPLE) -> list:
    """예시 표본 — 실제 판에서 큰 묶음 `WALK_CLUSTERS` 개에서 고르게 + 묶이지 않은 기록 몇. id 순으로 고르게
    띄워 뽑아 결정적이다. 판이 없으면(run_no 가 비면) 범위에서 고르게."""
    def spaced(xs: list, k: int) -> list:
        xs = sorted(xs, key=_order_key)
        if k >= len(xs):
            return xs
        return [xs[(i * len(xs)) // k] for i in range(k)]
    by: dict[Optional[int], list] = {}
    for i in ids:
        by.setdefault(run_no.get(i), []).append(i)
    big = sorted((no for no in by if no is not None), key=lambda no: (-len(by[no]), no))[:WALK_CLUSTERS]
    if not big:
        return spaced(ids, n)
    loose = by.get(None, [])
    n_loose = min(len(loose), max(0, n // 9))
    per, extra = divmod(n - n_loose, len(big))
    out: list = []
    for k, no in enumerate(big):
        out += spaced(by[no], per + (1 if k < extra else 0))
    out += spaced(loose, n_loose)
    return out


def _rl(vals: Optional[list], nd: int = 3) -> Optional[list]:
    return None if vals is None else [None if v is None else round(v, nd) for v in vals]


def _walk_cut(dist: list[list[float]]) -> dict:
    """표본 거리표 → 평균 연결의 합친 순서, 무리 수 k 마다의 실루엣, 고른 갈림 — `best_cut` 이 보는 그대로."""
    n = len(dist)
    merges = _linkage(dist)
    curve = []
    for k in range(2, min(4 * CLUSTER_K_MAX, n - 1) + 1):
        s, real = _silhouette(dist, _cut(n, merges, k))
        curve.append({"k": k, "real": real, "silhouette": round(s, 4)})
        if real > CLUSTER_K_MAX:
            break
    lab, real, s = best_cut(dist)
    return {"merges": [[round(h, 4), a, b] for h, a, b in merges], "curve": curve,
            "labels": lab, "clusters": real, "silhouette": round(s, 4)}


def walkthrough(group: list[Mapping], sample_ids: list, *, model: Optional[Mapping] = None) -> dict:
    """한 묶음 범위에서 「요약 → 정규화 → 채널 간 편차 → 평소 빼기 → 점수 → 거리 → 묶기」의 중간값 (SL-DOC-2).

    `group` = 범위 전체(`id` · `features`) — 기준선과 주성분 투영식은 범위 전체로 낸다(실제 판과 같다).
    `sample_ids` = 그림에 쓸 표본. 표본끼리의 거리로 `best_cut` 을 그대로 다시 돌린다 — 표본은 범위의 일부라
    갈림이 실제 판과 다를 수 있다(화면이 그 점을 말한다). 값은 모두 이 파일의 함수가 낸 것이다.
    """
    feats = {g["id"]: g["features"] for g in group}
    sample = [i for i in sample_ids if i in feats]
    base = skew_baseline(feats.values())
    if model is None:
        model = pca_model(feats.values())
    kind, keys = (base or {}).get("kind"), (base or {}).get("keys") or []
    nch = len(keys)

    recs = []
    for rid in sample:
        f = feats[rid]
        chans = f.get("channels") or {}
        k2, norm = _skew_norm(f)
        raw = {k: {"path": _rl((chans.get(k) or {}).get("path")), "grid": _rl((chans.get(k) or {}).get("grid")),
                   "stats": {s: (round(v, 6) if isinstance(v, float) else v)
                             for s, v in ((chans.get(k) or {}).get("stats") or {}).items()}} for k in sorted(chans)}
        nz = {k: {m: _rl(norm[k].get(m)) for m in ("x", "z", "path", "grid") if m in norm[k]} for k in sorted(norm)}
        rec = {"id": rid, "kind": k2, "raw": raw, "norm": nz,
               "dev": skew_profiles(f, None), "centred": skew_profiles(f, base)}
        if model is not None and model.get("components"):
            raw_p = _pca_raw(f, _model_mats(model))
            if raw_p and raw_p[0] == model["kind"] and list(raw_p[1]) == model["keys"]:
                vec = [v for _, _, vals in raw_p[2] for v in vals]
                resid = _pca_resid(model, raw_p[2])
                rec["pca"] = {"values": _rl(vec), "resid": _rl(resid, 4),
                              "coords": _rl(pca_coords(model, f), 4), "parts": pca_parts(model, f)}
        recs.append(rec)

    out: dict[str, Any] = {"n_group": len(group), "kind": kind, "channels": keys,
                           "records": recs, "methods": {}}
    if base:
        out["baseline"] = {k: {("line" if m == "path" else m): [round(base["mats"][m][j * nch + i], 3)
                                                                  for j in range(len(base["mats"][m]) // nch)]
                               for m in base["mats"]} for i, k in enumerate(keys)}
    if model is not None:
        total = model.get("total") or 0.0
        out["pca"] = {"layout": model["layout"], "factor": [round(x, 6) for x in model["factor"]],
                      "median": model["median"], "kept": len(model.get("components") or []),
                      "explained": [round(v / total, 4) if total else 0.0 for v in model.get("var") or []],
                      "axes": pca_axes(model, 2)["axes"] if model.get("components") else [],
                      "scatter": [{"id": g["id"], "xy": _rl((pca_coords(model, g["features"]) or [None, None])[:2], 4)}
                                  for g in group]}

    # 잣대마다 표본끼리의 점수 · 거리 · 묶기 — `cluster` 와 같은 식(비교할 수 없는 쌍은 거리 1).
    prep = [prepare_skew(feats[i], base) for i in sample]
    coords = [pca_coords(model, feats[i]) if model else None for i in sample]
    n = len(sample)
    for method in METHODS:
        sims: list[list[Optional[float]]] = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
        dist = [[0.0] * n for _ in range(n)]
        parts = {p: [[None] * n for _ in range(n)] for p in ("dx", "dz", "d2", "dp")} if method == "skew" else None
        for i in range(n):
            for j in range(i + 1, n):
                if method == "skew":
                    sim = skew_similarity(prep[i], prep[j])
                    s = sim["score"] if sim else None
                    for p in parts:
                        parts[p][i][j] = parts[p][j][i] = round(sim[p], 4) if sim and sim[p] is not None else None
                else:
                    s = pca_similarity(coords[i], coords[j])
                sims[i][j] = sims[j][i] = None if s is None else round(s, 4)
                dist[i][j] = dist[j][i] = 1.0 - s if s is not None else 1.0
        out["methods"][method] = {"score": sims, **({"parts": parts} if parts else {}), **_walk_cut(dist)}
    return out
