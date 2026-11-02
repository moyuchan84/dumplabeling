"""요청 본문 스키마 (pydantic)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


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
