"""서비스 상태."""
from __future__ import annotations

from fastapi import APIRouter

from ...connectors.fleet import FleetError, FleetUnavailable
from ...processing import shape
from .context import Ctx


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    fleet = ctx.fleet

    @r.get("/health")
    def health():
        out = {"db": True, "fleet": False, "ctx.fleet_url": ctx.fleet_url,
               "feature_version": shape.FEATURE_VERSION}
        try:
            out["fleet"] = fleet.ping()
        except (FleetUnavailable, FleetError) as exc:
            out["fleet_error"] = str(exc)
        return out

    return r
