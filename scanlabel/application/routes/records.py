"""기록 상세 — 지도에서 누른 점 하나 (SL-MAP)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ...processing import shape
from .context import Ctx
from .serializers import _labels_out, _r, _record_ref, _sim_out, _skew_out


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    store = ctx.store

    @r.get("/records/{record_id}")
    def record_detail(record_id: int, set_name: str = "dump", vs_server_id: Optional[int] = None,
                      vs_name: Optional[str] = None, vs_source: Optional[str] = None, method: str = "skew"):
        """풀의 기록 하나 — 지도에서 누른 점의 상세. `vs_*` 를 주면 그 기록과의 유사도(모양 · 채널 간 편차)와
        두 기록의 단면 · 칸 · 편차. 그림 재료(`profiles` · `skew`)는 기록마다 **제 것만으로** 정규화한다 — 상대가
        바뀌어도 그 기록의 그림은 같다. `method=pca` 면 그 결과의 묶음 · 자리와 함께, 이 기록이 범위의 평소와
        어디서 다른지(`pca`)와 상대와의 주성분 점수(`vs.pca_similarity`)."""
        shape.check_method(method)
        r = store.get_record(record_id)
        tree = store.tree()
        ids = store.label_of(set_name=r["set_name"], servername=r["servername"], name=r["name"],
                             source=r["source"])
        run = store.latest_run(r["set_name"], method)
        point = store.record_point(int(run["id"]), record_id) if run else None
        proj = ctx.proj_of(run, r)
        base = (proj or {}).get("baseline")
        pca = proj if proj is not None and proj.get("method") == "pca" else None
        out = {**_record_ref(r), "seen_at": r["seen_at"], "labels": _labels_out(tree, ids),
               "cluster_no": point["cluster_no"] if point else None,
               "run_id": int(run["id"]) if run else None, "method": method,
               "profiles": shape.normalized_profiles(r["features"], grid=True),
               "draw": shape.draw_shape(r["features"]),
               "skew": shape.skew_profiles(r["features"], base),
               "pca": shape.pca_parts(pca, r["features"]) if pca else None, "vs": None}
        if vs_server_id is not None and vs_name and vs_source:
            feats, _ = ctx.target_features(r["set_name"], vs_server_id, vs_name, vs_source)
            sim = shape.similarity(feats, r["features"])
            out["vs"] = {"similarity": None if sim is None else _sim_out(sim),
                         "skew_similarity": _skew_out(shape.skew_similarity(
                             shape.prepare_skew(feats, base), shape.prepare_skew(r["features"], base))),
                         "pca_similarity": None,
                         "profiles": shape.normalized_profiles(feats, grid=True),
                         "draw": shape.draw_shape(feats),
                         "skew": shape.skew_profiles(feats, base)}
            if pca:
                score = shape.pca_similarity(shape.pca_coords(pca, feats), shape.pca_coords(pca, r["features"]))
                out["vs"]["pca_similarity"] = None if score is None else {"score": _r(score)}
        return out

    return r
