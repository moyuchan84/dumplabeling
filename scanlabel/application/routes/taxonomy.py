"""라벨 트리 — 대 › 중 › 소 정의 (SL-TREE)."""
from __future__ import annotations

from fastapi import APIRouter

from .context import Ctx
from .schemas import DefIn, DefPatch


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    store = ctx.store

    @r.get("/label-defs")
    def list_defs(include_retired: bool = False):
        return {"defs": store.list_defs(include_retired=include_retired)}

    @r.post("/label-defs", status_code=201)
    def create_def(body: DefIn):
        return store.create_def(parent_id=body.parent_id, name=body.name, code=body.code,
                                description=body.description, case_id=body.case_id,
                                sort_no=body.sort_no, origin="added")

    @r.put("/label-defs/{def_id}")
    def update_def(def_id: int, body: DefPatch):
        return store.update_def(def_id, body.model_dump(exclude_unset=True))

    return r
