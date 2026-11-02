"""기록 라벨 · 닮은 기록 제안 · 사례 → 기록 (SL-LABEL · SL-SIM · SL-CASE)."""
from __future__ import annotations

from typing import Optional  # noqa: F401

from fastapi import APIRouter, Query

from ...processing import shape
from ..usecases import pool_rows
from .context import Ctx
from .schemas import LabelIn, SimilarIn
from .serializers import _labels_out, _level_out, _r, _sim_out


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    store, fleet, files = ctx.store, ctx.fleet, ctx.files

    @r.get("/labels")
    def file_labels(server_id: int, name: str = Query(..., min_length=1), set_name: str = "dump"):
        servername = fleet.servername(server_id)
        return {"labels": store.file_labels(servername, set_name, name)}

    @r.put("/labels")
    def put_label(body: LabelIn):
        servername = fleet.servername(body.server_id)
        if not body.label_ids:
            store.delete_label(servername, body.set_name, body.name, body.source)
            return {"label": None}
        # 저장은 항상 새로 읽는다 — 그 시점의 세대와 모양을 남긴다.
        f = files.get(body.server_id, body.set_name, body.name, fresh=True)
        scan = f.find(body.source)
        [rec] = pool_rows(f, [scan])
        row = store.upsert_label(
            servername=servername, server_id=body.server_id, set_name=body.set_name,
            name=body.name, source=body.source, sig=f.sig, scan_enum=rec["scan_enum"],
            meta=rec["meta"], label_ids=body.label_ids, features=rec["features"],
            feature_version=shape.FEATURE_VERSION)
        store.upsert_records([rec])                   # 라벨된 기록은 언제나 풀에도 있다 (SL-POOL-1)
        return {"label": row}

    @r.delete("/labels")
    def delete_label(server_id: int, source: str, name: str = Query(..., min_length=1),
                     set_name: str = "dump"):
        servername = fleet.servername(server_id)
        store.delete_label(servername, set_name, name, source)
        return {"deleted": True}

    @r.post("/labels/similar")
    def similar(body: SimilarIn):
        servername = fleet.servername(body.server_id)
        f = files.get(body.server_id, body.set_name, body.name)
        tree = store.tree()
        all_candidates = store.candidates(set_name=body.set_name)

        def others(source: str, scan_enum: str) -> list[dict]:
            return [c for c in all_candidates
                    if not (c["servername"] == servername and c["name"] == body.name
                            and c["source"] == source)
                    and (not body.same_scan_only or c["scan_enum"] == scan_enum)]

        def scan_enum_of(scan) -> str:
            return str((scan.meta or {}).get("scan_enum") or "")

        if body.source is None:
            # 파일 전체를 물었다 = 화면에서 그 파일을 열었다 — 그 기록들을 풀에 넣는다 (SL-POOL-1).
            rows = pool_rows(f)
            store.upsert_records(rows)
            out = []
            for s, rec in zip(f.scans, rows):
                res = shape.rank(rec["features"], others(s.source, scan_enum_of(s)), tree, k=0)
                sug = res["suggestion"]
                out.append({"source": s.source,
                            "suggestion": None if sug is None else {**sug, "score": _r(sug["score"])}})
            return {"sig": f.sig, "records": out}

        s = f.find(body.source)
        [rec] = pool_rows(f, [s])
        store.upsert_records([rec])
        feats = rec["features"]
        res = shape.rank(feats, others(s.source, scan_enum_of(s)), tree, k=body.k)
        matches = []
        for m in res["matches"]:
            c, sim = m["candidate"], m["similarity"]
            matches.append({
                "id": c["id"], "servername": c["servername"], "server_id": c["server_id"],
                "name": c["name"], "source": c["source"], "sig": c["sig"],
                "scan_enum": c["scan_enum"], "meta": c["meta"],
                "labels": [{"id": i, "level": int(tree.get(i)["level"]), "path": tree.path(i),
                            "case_id": tree.case_for(i)} for i in c["label_ids"] if i in tree],
                "similarity": _sim_out(sim),
                "profiles": shape.normalized_profiles(c["features"], sim["by_channel"].keys()),
            })
        sug = res["suggestion"]
        return {
            "sig": f.sig,
            "source": s.source,
            "scan_enum": scan_enum_of(s),
            "target_profiles": shape.normalized_profiles(feats),
            "matches": matches,
            "levels": {str(k): _level_out(v) for k, v in res["levels"].items()},
            "suggestion": None if sug is None else {**sug, "score": _r(sug["score"])},
            "compared": len(others(s.source, scan_enum_of(s))),
            "cases": [{**c, "score": _r(c["score"])} for c in res["cases"]],
            "cluster": ctx.record_cluster(body.set_name, servername, body.name, s.source, tree),
        }

    @r.get("/labels/by-case/{case_id}")
    def records_of_case(case_id: int, set_name: str = "dump", limit: int = Query(50, ge=1, le=500)):
        """이 사례를 가리키는 라벨(자기나 조상의 case_id)이 붙은 기록들 — 최근 것 먼저."""
        tree = store.tree()
        out = []
        for c in store.candidates(set_name=set_name):
            ids = [i for i in c["label_ids"] if i in tree and tree.case_for(i) == case_id]
            if ids:
                out.append({"id": c["id"], "servername": c["servername"], "server_id": c["server_id"],
                            "name": c["name"], "source": c["source"], "sig": c["sig"],
                            "scan_enum": c["scan_enum"], "meta": c["meta"], "updated_at": c["updated_at"],
                            "labels": _labels_out(tree, ids)})
        out.sort(key=lambda r: r["updated_at"], reverse=True)
        return {"case_id": case_id, "total": len(out), "records": out[:limit]}

    return r
