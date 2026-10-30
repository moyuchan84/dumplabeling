"""HTTP API — 호출 계약은 README.api.md.

`create_app(dsn, fleet_url)` 이 Store · Fleet 을 하나씩 들고 라우트를 붙인다. 에러 약속:
ValueError → 422 · KeyError → 404 · NotCached → 422 · FleetUnavailable → 502 · FleetError → 그 status.
라벨 라우트는 fleet 의 캐시만 읽는다 — FTP 를 일으키지 않는다(`connectors/fleet.py`).
"""
from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import timebase
from ..connectors.fleet import FileCache, Fleet, FleetError, FleetUnavailable, NotCached
from ..data.repository import Store
from ..processing import shape
from ..processing.tree import check_selection
from .usecases import pool_rows, run_clusters


class LabelIn(BaseModel):
    server_id: int
    set_name: str = "dump"
    name: str = Field(..., min_length=1, description="파일 이름 (fleet /list 의 name)")
    source: str = Field(..., min_length=1, description='기록 식별 "<tdf>::<dd>"')
    label_ids: list[int] = Field(default_factory=list, description="비우면 이 기록의 라벨을 지운다")


class DefIn(BaseModel):
    parent_id: Optional[int] = Field(None, description="없으면 대분류")
    name: str
    code: str = ""
    description: str = ""
    case_id: Optional[int] = None
    sort_no: Optional[int] = None


class DefPatch(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    case_id: Optional[int] = None
    retired: Optional[bool] = None
    sort_no: Optional[int] = None
    parent_id: Optional[int] = None


class SimilarIn(BaseModel):
    server_id: int
    set_name: str = "dump"
    name: str = Field(..., min_length=1)
    source: Optional[str] = Field(None, description="없으면 파일의 모든 기록에 대해 제안 1건씩")
    k: int = Field(10, ge=1, le=50)
    same_scan_only: bool = True


class ClusterRunIn(BaseModel):
    set_name: str = "dump"


class ClusterApplyIn(BaseModel):
    set_name: str = "dump"
    method: str = Field("skew", description="본 묶음 결과의 잣대 — skew | pca")
    run_id: int = Field(..., description="본 묶음 결과의 id — 그새 다시 묶였으면 422")
    label_ids: list[int] = Field(..., min_length=1)
    record_ids: Optional[list[int]] = Field(None, description="없으면 묶음 전체")
    only_unlabelled: bool = Field(True, description="이미 라벨된 구성원은 건드리지 않는다")


def _r(x: Optional[float], digits: int = 3) -> Optional[float]:
    return None if x is None else round(x, digits)


def _sim_out(sim: dict) -> dict:
    return {
        "score": _r(sim["score"]), "d1": _r(sim["d1"]), "d2": _r(sim["d2"]),
        "scale": _r(sim["scale"]),
        "by_channel": {k: {"score": _r(v["score"]), "d1": _r(v["d1"]), "d2": _r(v["d2"]),
                           "dx": _r(v["dx"]), "dz": _r(v["dz"]), "scale": _r(v["scale"])}
                       for k, v in sim["by_channel"].items()},
    }


def _skew_out(sim: Optional[dict]) -> Optional[dict]:
    """채널 간 편차 점수 — 묶음 · 지도가 쓰는 잣대 (SL-CLU-9)."""
    if sim is None:
        return None
    return {"score": _r(sim["score"]), "d1": _r(sim["d1"]), "d2": _r(sim["d2"]),
            "dx": _r(sim["dx"]), "dz": _r(sim["dz"]), "channels": sim["channels"]}


def _level_out(entries: list[dict]) -> list[dict]:
    return [{**e, "score": _r(e["score"])} for e in entries]


def _labels_out(tree, ids) -> list[dict]:
    return [{"id": int(i), "level": int(tree.get(int(i))["level"]), "path": tree.path(int(i)),
             "case_id": tree.case_for(int(i))} for i in ids or [] if int(i) in tree]


def _record_ref(r: dict) -> dict:
    return {"record_id": r["record_id"], "servername": r["servername"], "server_id": r["server_id"],
            "name": r["name"], "source": r["source"], "sig": r["sig"], "scan_enum": r["scan_enum"],
            "chuck_id": r.get("chuck_id") or "", "sensor_id": r.get("sensor_id") or "", "meta": r["meta"]}


def _show_channel(features: Optional[dict]) -> Optional[str]:
    """묶음을 그려 보일 채널 — 단면(또는 선 스캔의 곡선)이 있는 채널 중 이름 순 첫째. 한 묶음 안에서는 모두 같은 채널."""
    chans = (features or {}).get("channels") or {}
    keys = sorted(k for k, v in chans.items() if (v or {}).get("grid") or (v or {}).get("path"))
    return keys[0] if keys else None


def _category_out(cat: dict) -> dict:
    def entry(e: dict) -> dict:
        return {**e, "share": _r(e["share"])}
    return {"labelled": cat["labelled"], "unlabelled": cat["unlabelled"],
            "levels": {str(k): [entry(e) for e in v] for k, v in cat["levels"].items()},
            "category": None if cat["category"] is None else entry(cat["category"])}


def create_app(dsn: Optional[str] = None, fleet_url: Optional[str] = None, *,
               fleet_transport=None) -> FastAPI:
    dsn = dsn or os.environ.get("SCANLABEL_DB_DSN", "")
    fleet_url = fleet_url or os.environ.get("FLEET_API_URL", "http://localhost:8000")
    if not dsn:
        raise RuntimeError("SCANLABEL_DB_DSN 이 필요하다")
    store = Store(dsn)
    fleet = Fleet(fleet_url, transport=fleet_transport)
    files = FileCache(fleet)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()
        fleet.close()

    app = FastAPI(title="scanlabel", version="0.1", lifespan=lifespan)
    app.state.store, app.state.fleet = store, fleet
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ValueError)
    def _value(_: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    def _key(_: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0] if exc.args else exc)})

    @app.exception_handler(FleetUnavailable)
    def _down(_: Request, exc: FleetUnavailable):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(FleetError)
    def _fleet(_: Request, exc: FleetError):
        status = exc.status if exc.status in (404, 422) else 502
        return JSONResponse(status_code=status, content={"detail": exc.detail})

    # NotCached 는 ValueError 의 하위라 422 로 간다 — 따로 적을 것이 없다.
    _ = NotCached

    @app.get("/health")
    def health():
        out = {"db": True, "fleet": False, "fleet_url": fleet_url,
               "feature_version": shape.FEATURE_VERSION}
        try:
            out["fleet"] = fleet.ping()
        except (FleetUnavailable, FleetError) as exc:
            out["fleet_error"] = str(exc)
        return out

    # ─── 라벨 트리 ──────────────────────────────────────────────────────────

    @app.get("/label-defs")
    def list_defs(include_retired: bool = False):
        return {"defs": store.list_defs(include_retired=include_retired)}

    @app.post("/label-defs", status_code=201)
    def create_def(body: DefIn):
        return store.create_def(parent_id=body.parent_id, name=body.name, code=body.code,
                                description=body.description, case_id=body.case_id,
                                sort_no=body.sort_no, origin="added")

    @app.put("/label-defs/{def_id}")
    def update_def(def_id: int, body: DefPatch):
        return store.update_def(def_id, body.model_dump(exclude_unset=True))

    # ─── 기록 라벨 ──────────────────────────────────────────────────────────

    @app.get("/labels")
    def file_labels(server_id: int, name: str = Query(..., min_length=1), set_name: str = "dump"):
        servername = fleet.servername(server_id)
        return {"labels": store.file_labels(servername, set_name, name)}

    @app.put("/labels")
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

    @app.delete("/labels")
    def delete_label(server_id: int, source: str, name: str = Query(..., min_length=1),
                     set_name: str = "dump"):
        servername = fleet.servername(server_id)
        store.delete_label(servername, set_name, name, source)
        return {"deleted": True}

    # ─── 유사 ──────────────────────────────────────────────────────────────

    @app.post("/labels/similar")
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
            "cluster": record_cluster(body.set_name, servername, body.name, s.source, tree),
        }

    # ─── 사례 → 기록 (SL-CASE-2) ────────────────────────────────────────────

    @app.get("/labels/by-case/{case_id}")
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

    # ─── 묶음 (SL-CLU) ──────────────────────────────────────────────────────

    def record_cluster(set_name: str, servername: str, name: str, source: str, tree) -> Optional[dict]:
        hit = store.record_cluster(set_name=set_name, servername=servername, name=name, source=source)
        if hit is None:
            return None
        members = store.run_members(int(hit["run_id"]), cluster_no=int(hit["cluster_no"]))
        labels = store.labels_by_record(set_name=set_name)
        cat = shape.cluster_category(
            [labels.get((m["servername"], m["name"], m["source"]), []) for m in members], tree)
        return {"run_id": int(hit["run_id"]), "cluster_no": int(hit["cluster_no"]),
                "size": len(members), "scan_enum": members[0]["scan_enum"] if members else "",
                "chuck_id": members[0]["chuck_id"] if members else "",
                "sensor_id": members[0]["sensor_id"] if members else "",
                **_category_out(cat)}

    def proj_of(run: Optional[dict], row: dict) -> Optional[dict]:
        """그 기록의 묶음 범위의 지도 투영식 — 결과가 없거나 범위의 기록이 너무 적었으면 None."""
        return store.run_map(int(run["id"]), shape.group_key(*shape.group_of(row))) if run else None

    def baseline_of(run: Optional[dict], row: dict) -> Optional[dict]:
        """그 기록의 묶음 범위에서 최신 결과가 뺀 「평소 어긋남」 — 화면도 묶음이 본 그대로 그린다 (SL-CLU-9)."""
        return (proj_of(run, row) or {}).get("baseline")

    def run_out(set_name: str, method: str) -> Optional[dict]:
        run = store.latest_run(set_name, method)
        if run is None:
            return None
        return {"id": run["id"], "method": run["method"], "created_at": run["created_at"],
                "records": run["n_records"],
                "clusters": run["n_clusters"], "unclustered": run["n_unclustered"],
                "truncated": run["truncated"], "threshold": _r(run["threshold"]),
                "min_size": run["min_size"],
                "stale": store.pool_changed_since(set_name=set_name, seq=int(run["pool_seq"]))}

    def clusters_view(set_name: str, scan_enum: Optional[str], method: str) -> dict:
        shape.check_method(method)
        # 일의 상태를 결과보다 먼저 읽는다 — 거꾸로면 그 사이에 끝난 일이 「done」인데 결과는 없는 응답이 된다.
        job = jobs_state.get(set_name)
        run = run_out(set_name, method)
        pool_total = store.pool_count(set_name)
        if run is None:
            return {"run": None, "pool": pool_total, "clusters": [], "job": job}
        tree = store.tree()
        labels = store.labels_by_record(set_name=set_name)
        groups: dict[int, list[dict]] = {}
        for m in store.run_members(run["id"]):
            groups.setdefault(int(m["cluster_no"]), []).append(m)
        rep_feats = store.features_of([g[0]["record_id"] for g in groups.values()])
        full_run = store.latest_run(set_name, method)
        out = []
        for no, members in sorted(groups.items()):
            rep = members[0]
            if scan_enum is not None and rep["scan_enum"] != scan_enum:
                continue
            cat = shape.cluster_category(
                [labels.get((m["servername"], m["name"], m["source"]), []) for m in members], tree)
            feats = rep_feats.get(rep["record_id"])
            ch = _show_channel(feats)
            out.append({"cluster_no": no, "scan_enum": rep["scan_enum"], "chuck_id": rep["chuck_id"],
                        "sensor_id": rep["sensor_id"], "size": len(members),
                        "min_score": _r(min(m["score"] for m in members)),
                        "representative": _record_ref(rep), "channel": ch,
                        "profile": shape.normalized_profiles(feats, [ch]).get(ch) if ch else None,
                        "skew": shape.skew_profiles(feats or {}, baseline_of(full_run, rep)),
                        **_category_out(cat)})
        return {"run": run, "pool": pool_total, "clusters": out, "job": job}

    # 다시 묶기는 수십 초 걸릴 수 있다 — 요청 하나에 매달면 앞단 프록시가 먼저 끊는다(socket hang up).
    # 그래서 뒤에서 돌리고 바로 202 를 돌려준다. 상태는 이 프로세스의 메모리에만 있다(SL-CLU-8).
    jobs_state: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    def _run_job(set_name: str) -> None:
        try:
            run_clusters(store, set_name=set_name)
            state = {"state": "done", "error": None}
        except Exception as exc:                        # 실패는 화면이 말할 수 있게 남긴다
            state = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
        with jobs_lock:
            jobs_state[set_name] = {**jobs_state[set_name], **state, "finished_at": timebase.now_iso()}

    @app.get("/clusters")
    def list_clusters(set_name: str = "dump", scan_enum: Optional[str] = None, method: str = "skew"):
        return clusters_view(set_name, scan_enum, method)

    @app.post("/clusters/run", status_code=202)
    def rerun_clusters(body: ClusterRunIn):
        """다시 묶기를 **시작**한다 — 끝나기를 기다리지 않는다. 이미 돌고 있으면 그것을 그대로 알려 준다."""
        with jobs_lock:
            cur = jobs_state.get(body.set_name)
            if cur and cur["state"] == "running":
                return {"job": cur}
            jobs_state[body.set_name] = {"state": "running", "started_at": timebase.now_iso(),
                                         "finished_at": None, "error": None}
            job = jobs_state[body.set_name]
        threading.Thread(target=_run_job, args=(body.set_name,), name=f"cluster-{body.set_name}",
                         daemon=True).start()
        return {"job": job}

    def current_run(set_name: str, run_id: Optional[int], method: str) -> dict:
        shape.check_method(method)
        run = store.latest_run(set_name, method)
        if run is None:
            raise KeyError("아직 묶은 적이 없다 — 「다시 묶기」를 먼저")
        if run_id is not None and int(run["id"]) != run_id:
            raise ValueError("그새 묶음이 다시 계산됐다 — 목록을 새로 읽어 달라")
        return run

    # 경로는 아래에서 붙인다 — `/clusters/map` 보다 먼저 붙으면 "map" 을 묶음 번호로 읽어 422 가 된다.
    def get_cluster(cluster_no: int, set_name: str = "dump", run_id: Optional[int] = None,
                    method: str = "skew"):
        run = current_run(set_name, run_id, method)
        members = store.run_members(int(run["id"]), cluster_no=cluster_no, with_features=True)
        if not members:
            raise KeyError(f"없는 묶음: {cluster_no}")
        tree = store.tree()
        labels = store.labels_by_record(set_name=set_name)
        member_labels = [labels.get((m["servername"], m["name"], m["source"]), []) for m in members]
        ch = _show_channel(members[0]["features"])
        return {
            "run_id": int(run["id"]), "method": method, "cluster_no": cluster_no,
            "scan_enum": members[0]["scan_enum"],
            "chuck_id": members[0]["chuck_id"], "sensor_id": members[0]["sensor_id"], "size": len(members),
            "channel": ch, "skew": shape.skew_profiles(members[0]["features"], baseline_of(run, members[0])),
            **_category_out(shape.cluster_category(member_labels, tree)),
            "members": [{**_record_ref(m), "score": _r(m["score"]), "representative": m["representative"],
                         "labels": _labels_out(tree, ids),
                         "profile": shape.normalized_profiles(m["features"], [ch]).get(ch) if ch else None}
                        for m, ids in zip(members, member_labels)],
        }

    # ─── 묶음 지도 · 기록 상세 (SL-MAP) ────────────────────────────────────

    def target_features(set_name: str, server_id: int, name: str, source: str) -> tuple[dict, Optional[dict]]:
        """지도 위에 놓을 기록 — 풀에 있으면 그 행, 없으면 fleet 캐시에서 읽어 특징만."""
        servername = fleet.servername(server_id)
        row = store.find_record(set_name=set_name, servername=servername, name=name, source=source)
        if row is not None:
            return row["features"], row
        f = files.get(server_id, set_name, name)
        [rec] = pool_rows(f, [f.find(source)])
        return rec["features"], {"scan_enum": rec["scan_enum"], "chuck_id": rec["chuck_id"],
                                 "sensor_id": rec["sensor_id"], "record_id": None}

    @app.get("/clusters/map")
    def cluster_map(set_name: str = "dump", scan_enum: Optional[str] = None, chuck_id: Optional[str] = None,
                    sensor_id: Optional[str] = None, server_id: Optional[int] = None, name: Optional[str] = None,
                    source: Optional[str] = None, method: str = "skew"):
        """최신 묶음 결과의 2D 지도 한 장(묶음 범위 = 스캔 종류 · 척 · 센서 하나) + 지금 기록의 자리.

        `method=pca` 면 두 축이 첫째 · 둘째 주성분이고, 축마다 무엇을 보는지(`axes`)를 함께 준다.
        """
        shape.check_method(method)
        run = store.latest_run(set_name, method)
        if run is None:
            raise KeyError("아직 묶은 적이 없다 — 「다시 묶기」를 먼저")
        run_id = int(run["id"])
        kinds = store.map_kinds(run_id)
        target = None
        target_group: Optional[tuple[str, str, str]] = None
        feats = None
        trow = None
        if server_id is not None and name and source:
            feats, trow = target_features(set_name, server_id, name, source)
            target_group = shape.group_of(trow)
        if scan_enum is not None:
            group = (scan_enum, chuck_id or "", sensor_id or "")
        elif target_group is not None:
            group = target_group
        else:
            group = shape.group_of(kinds[0]) if kinds else ("", "", "")
        key = shape.group_key(*group)
        proj = store.run_map(run_id, key)
        tree = store.tree()
        labels = store.labels_by_record(set_name=set_name)

        if feats is not None and target_group == group:
            hit = store.record_point(run_id, trow["record_id"]) if trow.get("record_id") else None
            if hit is not None:
                target = {"x": round(hit["x"], 4), "y": round(hit["y"], 4), "cluster_no": hit["cluster_no"],
                          "record_id": trow["record_id"], "in_run": True}
            elif proj is not None:
                lms = proj.get("landmarks") or []
                lf = store.features_of(lms)
                xy = shape.project_features(proj, feats, [lf[i] for i in lms if i in lf])
                if xy is not None:
                    target = {"x": round(xy[0], 4), "y": round(xy[1], 4), "cluster_no": None,
                              "record_id": trow.get("record_id"), "in_run": False}

        clusters: dict[int, dict] = {}
        points = []
        for p in store.map_points(run_id, *group):
            no = p["cluster_no"]
            points.append({"id": p["record_id"], "x": round(p["x"], 4), "y": round(p["y"], 4),
                           "c": no, "l": bool(p["labelled"])})
            if no is not None:
                clusters.setdefault(no, []).append(p)
        cl_out = []
        for no, ps in sorted(clusters.items()):
            cat = shape.cluster_category(
                [labels.get((m["servername"], m["name"], m["source"]), []) for m in ps], tree)["category"]
            cl_out.append({"cluster_no": no, "size": len(ps),
                           "category": None if cat is None else cat["path"]})
        return {"run_id": run_id, "method": method,
                "scan_enum": group[0], "chuck_id": group[1], "sensor_id": group[2],
                "kinds": [{"scan_enum": k["scan_enum"], "chuck_id": k["chuck_id"], "sensor_id": k["sensor_id"],
                           "n": k["n"]} for k in kinds],
                "points": points, "clusters": cl_out, "target": target,
                "target_kind": None if target_group is None else target_group[0],
                "target_chuck": None if target_group is None else target_group[1],
                "target_sensor": None if target_group is None else target_group[2],
                "has_map": proj is not None,
                "axes": shape.pca_axes(proj) if proj is not None and method == "pca" else None}

    @app.get("/records/{record_id}")
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
        proj = proj_of(run, r)
        base = (proj or {}).get("baseline")
        pca = proj if proj is not None and proj.get("method") == "pca" else None
        out = {**_record_ref(r), "seen_at": r["seen_at"], "labels": _labels_out(tree, ids),
               "cluster_no": point["cluster_no"] if point else None,
               "run_id": int(run["id"]) if run else None, "method": method,
               "profiles": shape.normalized_profiles(r["features"], grid=True),
               "skew": shape.skew_profiles(r["features"], base),
               "pca": shape.pca_parts(pca, r["features"]) if pca else None, "vs": None}
        if vs_server_id is not None and vs_name and vs_source:
            feats, _ = target_features(r["set_name"], vs_server_id, vs_name, vs_source)
            sim = shape.similarity(feats, r["features"])
            out["vs"] = {"similarity": None if sim is None else _sim_out(sim),
                         "skew_similarity": _skew_out(shape.skew_similarity(
                             shape.prepare_skew(feats, base), shape.prepare_skew(r["features"], base))),
                         "pca_similarity": None,
                         "profiles": shape.normalized_profiles(feats, grid=True),
                         "skew": shape.skew_profiles(feats, base)}
            if pca:
                score = shape.pca_similarity(shape.pca_coords(pca, feats), shape.pca_coords(pca, r["features"]))
                out["vs"]["pca_similarity"] = None if score is None else {"score": _r(score)}
        return out

    app.get("/clusters/{cluster_no}")(get_cluster)

    @app.post("/clusters/{cluster_no}/apply")
    def apply_cluster(cluster_no: int, body: ClusterApplyIn):
        """묶음 구성원에게 라벨을 한 번에 붙인다 — 풀의 스냅샷을 쓰므로 fleet 을 부르지 않는다 (SL-CLU-5)."""
        run = current_run(body.set_name, body.run_id, body.method)
        members = store.run_members(int(run["id"]), cluster_no=cluster_no, with_features=True)
        if not members:
            raise KeyError(f"없는 묶음: {cluster_no}")
        tree = store.tree()
        ids = check_selection(tree, body.label_ids)      # 붙이기 전에 한 번 — 하나도 쓰지 않고 422
        wanted = set(body.record_ids) if body.record_ids is not None else None
        labels = store.labels_by_record(set_name=body.set_name)
        applied = skipped = 0
        for m in members:
            if wanted is not None and m["record_id"] not in wanted:
                continue
            if body.only_unlabelled and labels.get((m["servername"], m["name"], m["source"])):
                skipped += 1
                continue
            store.upsert_label(
                servername=m["servername"], server_id=int(m["server_id"]), set_name=m["set_name"],
                name=m["name"], source=m["source"], sig=m["sig"], scan_enum=m["scan_enum"],
                meta=m["meta"], label_ids=ids, features=m["features"],
                feature_version=shape.FEATURE_VERSION)
            applied += 1
        return {"applied": applied, "skipped": skipped}

    return app


def __getattr__(name: str):
    # `uvicorn scanlabel.application.api:app` — import 만으로 DB 에 붙지 않게 지연 생성 (fleet 과 같은 방식).
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
