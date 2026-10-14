"""HTTP API — 호출 계약은 README.api.md.

`create_app(dsn, fleet_url)` 이 Store · Fleet 을 하나씩 들고 라우트를 붙인다. 에러 약속:
ValueError → 422 · KeyError → 404 · NotCached → 422 · FleetUnavailable → 502 · FleetError → 그 status.
라벨 라우트는 fleet 의 캐시만 읽는다 — FTP 를 일으키지 않는다(`fleet_client`).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import shape
from .fleet_client import FileCache, Fleet, FleetError, FleetUnavailable, NotCached, meta_summary
from .store import Store


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


def _r(x: Optional[float], digits: int = 3) -> Optional[float]:
    return None if x is None else round(x, digits)


def _sim_out(sim: dict) -> dict:
    return {
        "score": _r(sim["score"]), "d1": _r(sim["d1"]), "d2": _r(sim["d2"]),
        "scale": _r(sim["scale"]),
        "by_channel": {k: {"score": _r(v["score"]), "d1": _r(v["d1"]), "d2": _r(v["d2"]),
                           "scale": _r(v["scale"])} for k, v in sim["by_channel"].items()},
    }


def _level_out(entries: list[dict]) -> list[dict]:
    return [{**e, "score": _r(e["score"])} for e in entries]


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
        meta = meta_summary(scan.meta)
        row = store.upsert_label(
            servername=servername, server_id=body.server_id, set_name=body.set_name,
            name=body.name, source=body.source, sig=f.sig,
            scan_enum=str(meta.get("scan_enum") or ""), meta=meta, label_ids=body.label_ids,
            features=shape.record_features(scan.scan), feature_version=shape.FEATURE_VERSION)
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
            out = []
            for s in f.scans:
                feats = shape.record_features(s.scan)
                res = shape.rank(feats, others(s.source, scan_enum_of(s)), tree, k=0)
                sug = res["suggestion"]
                out.append({"source": s.source,
                            "suggestion": None if sug is None else {**sug, "score": _r(sug["score"])}})
            return {"sig": f.sig, "records": out}

        s = f.find(body.source)
        feats = shape.record_features(s.scan)
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
                "paths": shape.normalized_paths(c["features"], sim["by_channel"].keys()),
            })
        sug = res["suggestion"]
        return {
            "sig": f.sig,
            "source": s.source,
            "scan_enum": scan_enum_of(s),
            "target_paths": shape.normalized_paths(feats),
            "matches": matches,
            "levels": {str(k): _level_out(v) for k, v in res["levels"].items()},
            "suggestion": None if sug is None else {**sug, "score": _r(sug["score"])},
            "compared": len(others(s.source, scan_enum_of(s))),
        }

    return app


def __getattr__(name: str):
    # `uvicorn scanlabel.api:app` — import 만으로 DB 에 붙지 않게 지연 생성 (fleet 과 같은 방식).
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
