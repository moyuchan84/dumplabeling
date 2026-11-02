"""라우트들이 함께 쓰는 것 — Store · Fleet · 파일 캐시와, 여러 라우트가 부르는 조회 도우미."""
from __future__ import annotations

from typing import Optional

from ...connectors.fleet import FileCache, Fleet
from ...data.repository import Store
from ...processing import shape
from ..usecases import pool_rows
from .serializers import _category_out


class Ctx:
    def __init__(self, store: Store, fleet: Fleet, files: FileCache, fleet_url: str):
        self.store, self.fleet, self.files, self.fleet_url = store, fleet, files, fleet_url

    def record_cluster(self, set_name: str, servername: str, name: str, source: str, tree) -> Optional[dict]:
        hit = self.store.record_cluster(set_name=set_name, servername=servername, name=name, source=source)
        if hit is None:
            return None
        members = self.store.run_members(int(hit["run_id"]), cluster_no=int(hit["cluster_no"]))
        labels = self.store.labels_by_record(set_name=set_name)
        cat = shape.cluster_category(
            [labels.get((m["servername"], m["name"], m["source"]), []) for m in members], tree)
        return {"run_id": int(hit["run_id"]), "cluster_no": int(hit["cluster_no"]),
                "size": len(members), "scan_enum": members[0]["scan_enum"] if members else "",
                "chuck_id": members[0]["chuck_id"] if members else "",
                "sensor_id": members[0]["sensor_id"] if members else "",
                **_category_out(cat)}

    def proj_of(self, run: Optional[dict], row: dict) -> Optional[dict]:
        """그 기록의 묶음 범위의 지도 투영식 — 결과가 없거나 범위의 기록이 너무 적었으면 None."""
        return self.store.run_map(int(run["id"]), shape.group_key(*shape.group_of(row))) if run else None

    def shapes_of(self, run_id: int) -> dict[int, dict]:
        """그 판의 묶음별 평균 그림. 그림 없이 저장된 판(0009 전)이면 **처음 읽을 때 한 번** 구성원에서 내어
        채워 둔다 — 「다시 묶기」를 누르지 않아도 목록 · 지도에 그래프가 나오게 (SL-DRAW-2)."""
        shapes = self.store.cluster_shapes(run_id)
        if shapes:
            return shapes
        groups: dict[int, list[dict]] = {}
        for m in self.store.run_members(run_id, with_features=True):
            groups.setdefault(int(m["cluster_no"]), []).append(m["features"])
        shapes = {no: shape.mean_shape(fs) for no, fs in groups.items()}
        self.store.save_cluster_shapes(run_id, shapes)
        return shapes

    def current_run(self, set_name: str, run_id: Optional[int], method: str) -> dict:
        shape.check_method(method)
        run = self.store.latest_run(set_name, method)
        if run is None:
            raise KeyError("아직 묶은 적이 없다 — 「다시 묶기」를 먼저")
        if run_id is not None and int(run["id"]) != run_id:
            raise ValueError("그새 묶음이 다시 계산됐다 — 목록을 새로 읽어 달라")
        return run

    def target_features(self, set_name: str, server_id: int, name: str, source: str) -> tuple[dict, Optional[dict]]:
        """지도 위에 놓을 기록 — 풀에 있으면 그 행, 없으면 fleet 캐시에서 읽어 특징만."""
        servername = self.fleet.servername(server_id)
        row = self.store.find_record(set_name=set_name, servername=servername, name=name, source=source)
        if row is not None:
            return row["features"], row
        f = self.files.get(server_id, set_name, name)
        [rec] = pool_rows(f, [f.find(source)])
        return rec["features"], {"scan_enum": rec["scan_enum"], "chuck_id": rec["chuck_id"],
                                 "sensor_id": rec["sensor_id"], "record_id": None}
