"""읽는 법 문서 · 실제 기록으로 따라가기 (SL-DOC-1 · SL-DOC-2)."""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ...processing import shape
from ..web import FLOW, render_guide
from .context import Ctx


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    store = ctx.store

    @r.get("/guide", response_class=HTMLResponse)
    def guide():
        """「묶음」 탭 읽는 법 — 리뷰하는 사람용 문서. 숫자는 지금 상수로 채운다 (SL-DOC-1)."""
        return HTMLResponse(render_guide())

    @r.get("/guide/flow", response_class=HTMLResponse)
    def guide_flow():
        """데이터가 묶음이 되기까지 — 실제 기록으로 단계마다 그려 보는 설명 (SL-DOC-2). 데이터는 /guide/example."""
        return HTMLResponse(render_guide(FLOW))

    walk_cache: dict[tuple, dict] = {}
    walk_lock = threading.Lock()

    @r.get("/guide/example")
    def guide_example(set_name: str = "dump", group: Optional[str] = None):
        """읽는 법의 실제 예시 — 한 묶음 범위의 표본으로 변환 · 점수 · 묶기의 중간값 (SL-DOC-2).

        기준선 · 투영식은 범위 전체로 낸다(실제 판과 같다). 같은 판 · 같은 풀 크기면 이 프로세스가 기억해 둔다.
        """
        runs = {m: store.latest_run(set_name, m) for m in shape.METHODS}
        pool_n = store.pool_count(set_name)
        ck = (set_name, group, pool_n, tuple(int(r["id"]) if r else None for r in runs.values()))
        with walk_lock:
            if ck in walk_cache:
                return walk_cache[ck]
        rows, _, _, _ = store.pool(set_name=set_name, per_scan_limit=shape.CLUSTER_MAX_N)
        kinds: dict[str, list[dict]] = {}
        for r in rows:
            kinds.setdefault(shape.group_key(*shape.group_of(r)), []).append(r)
        if not kinds:
            raise KeyError("풀에 기록이 없다 — 기록을 먼저 모아 달라")
        groups = sorted(({"key": k, "size": len(v)} for k, v in kinds.items()), key=lambda g: (-g["size"], g["key"]))
        key = group or groups[0]["key"]
        if key not in kinds:
            raise KeyError(f"그런 묶음 범위가 없다: {key}")
        group_rows = kinds[key]
        run_no: dict[str, dict[int, int]] = {}
        refs: dict[int, dict] = {}
        for m, run in runs.items():
            run_no[m] = {}
            for mem in store.run_members(int(run["id"])) if run else []:
                if shape.group_key(mem["scan_enum"], mem["chuck_id"], mem["sensor_id"]) == key:
                    run_no[m][int(mem["record_id"])] = int(mem["cluster_no"])
                    refs[int(mem["record_id"])] = {"name": mem["name"], "source": mem["source"],
                                                   "servername": mem["servername"]}
        model = None
        if runs.get("pca"):
            proj = store.run_map(int(runs["pca"]["id"]), key)
            model = proj if proj and proj.get("method") == "pca" else None
        ids = [r["id"] for r in group_rows]
        sample = shape.walk_sample(ids, run_no.get("skew") or {})
        out = shape.walkthrough(group_rows, sample, model=model)
        tree = store.tree()
        labels = store.labels_by_record_id(set_name=set_name)
        for rec in out["records"]:
            rid = int(rec["id"])
            rec["ref"] = refs.get(rid)
            rec["run_no"] = {m: run_no[m].get(rid) for m in run_no}
            rec["label"] = [tree.path(i) for i in labels.get(rid, []) if i in tree] or None
        if "pca" in out:
            for p in out["pca"]["scatter"]:
                p["run_no"] = {m: run_no[m].get(int(p["id"])) for m in run_no}
        out.update(set_name=set_name, group=key, groups=groups,
                   runs={m: ({"id": r["id"], "created_at": r["created_at"], "clusters": r["n_clusters"]} if r else None)
                         for m, r in runs.items()},
                   consts={n: getattr(shape, n) for n in (
                       "PATH_N", "GRID_N", "PCA_PATH", "PCA_GRID", "SKEW_FLOOR", "PCA_FLOOR", "W_1D", "W_2D",
                       "W_SEQ", "CLUSTER_K_MAX", "CLUSTER_MIN_SIZE", "CLUSTER_MAX_N", "PCA_VAR", "PCA_MAX_K")}
                   | {"PCA_W": shape.PCA_W, "PCA_MAT_W": shape.PCA_MAT_W})
        with walk_lock:
            if len(walk_cache) >= 8:                # 판이 바뀌면 옛것은 쓸 일이 없다 — 작게 둔다
                walk_cache.clear()
            walk_cache[ck] = out
        return out

    return r
