"""HTTP API — 호출 계약은 README.api.md.

`create_app(dsn, fleet_url)` 이 Store · Fleet 을 하나씩 들고 `routes/` 의 라우터를 모아 붙인다. 에러 약속:
ValueError → 422 · KeyError → 404 · NotCached → 422 · FleetUnavailable → 502 · FleetError → 그 status.
라벨 라우트는 fleet 의 캐시만 읽는다 — FTP 를 일으키지 않는다(`connectors/fleet.py`).

라우트는 영역별 파일이다 — `routes/{system,taxonomy,labels,clusters,records,guide}.py`. 여기는 조립만 한다.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..connectors.fleet import FileCache, Fleet, FleetError, FleetUnavailable
from ..data.repository import Store
from .routes import clusters, guide, labels, records, system, taxonomy
from .routes.context import Ctx


def create_app(dsn: Optional[str] = None, fleet_url: Optional[str] = None, *,
               fleet_transport=None) -> FastAPI:
    dsn = dsn or os.environ.get("SCANLABEL_DB_DSN", "")
    fleet_url = fleet_url or os.environ.get("FLEET_API_URL", "http://localhost:8000")
    if not dsn:
        raise RuntimeError("SCANLABEL_DB_DSN 이 필요하다")
    store = Store(dsn)
    fleet = Fleet(fleet_url, transport=fleet_transport)
    ctx = Ctx(store, fleet, FileCache(fleet), fleet_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()
        fleet.close()

    app = FastAPI(title="scanlabel", version="0.1", lifespan=lifespan)
    app.state.store, app.state.fleet = store, fleet
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # NotCached 는 ValueError 의 하위라 422 로 간다 — 따로 적을 것이 없다.
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

    for module in (guide, system, taxonomy, labels, clusters, records):
        app.include_router(module.router(ctx))
    return app


def __getattr__(name: str):
    # `uvicorn scanlabel.application.api:app` — import 만으로 DB 에 붙지 않게 지연 생성 (fleet 과 같은 방식).
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
