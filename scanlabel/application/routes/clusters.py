"""묶음(클러스터) — 목록 · 다시 묶기 · 지도 · 구성원 일괄 라벨 (SL-CLU · SL-MAP)."""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter

from ... import timebase
from ...processing import shape
from ...processing.tree import check_selection
from ..usecases import run_clusters
from .context import Ctx
from .schemas import ClusterApplyIn, ClusterRunIn
from .serializers import _category_out, _labels_out, _r, _record_ref


def router(ctx: Ctx) -> APIRouter:
    r = APIRouter()
    store = ctx.store

    jobs_state: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    def run_out(set_name: str, method: str) -> Optional[dict]:
        run = store.latest_run(set_name, method)
        if run is None:
            return None
        return {"id": run["id"], "method": run["method"], "created_at": run["created_at"],
                "records": run["n_records"],
                "clusters": run["n_clusters"], "unclustered": run["n_unclustered"],
                "truncated": run["truncated"],
                "min_size": run["min_size"], "changes": run.get("changes"),
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
        shapes = ctx.shapes_of(int(run["id"]))
        out = []
        # 큰 묶음부터 — 번호는 이어받은 것이라 크기 순이 아니다(SL-CLU-13).
        for no, members in sorted(groups.items(), key=lambda t: (-len(t[1]), t[0])):
            rep = members[0]
            if scan_enum is not None and rep["scan_enum"] != scan_enum:
                continue
            cat = shape.cluster_category(
                [labels.get((m["servername"], m["name"], m["source"]), []) for m in members], tree)
            out.append({"cluster_no": no, "scan_enum": rep["scan_enum"], "chuck_id": rep["chuck_id"],
                        "sensor_id": rep["sensor_id"], "size": len(members),
                        "representative": _record_ref(rep),
                        "shape": shape.thin_shape(shapes.get(no)),
                        **_category_out(cat)})
        return {"run": run, "pool": pool_total, "clusters": out, "job": job}

    # 다시 묶기는 수십 초 걸릴 수 있다 — 요청 하나에 매달면 앞단 프록시가 먼저 끊는다(socket hang up).
    # 그래서 뒤에서 돌리고 바로 202 를 돌려준다. 상태는 이 프로세스의 메모리에만 있다(SL-CLU-8).

    def _run_job(set_name: str) -> None:
        try:
            run_clusters(store, set_name=set_name)
            state = {"state": "done", "error": None}
        except Exception as exc:                        # 실패는 화면이 말할 수 있게 남긴다
            state = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
        with jobs_lock:
            jobs_state[set_name] = {**jobs_state[set_name], **state, "finished_at": timebase.now_iso()}

    @r.get("/clusters")
    def list_clusters(set_name: str = "dump", scan_enum: Optional[str] = None, method: str = "skew"):
        return clusters_view(set_name, scan_enum, method)

    @r.post("/clusters/run", status_code=202)
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

    @r.get("/clusters/map")
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
            feats, trow = ctx.target_features(set_name, server_id, name, source)
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
        shapes = ctx.shapes_of(run_id)
        for no, ps in sorted(clusters.items()):
            cat = shape.cluster_category(
                [labels.get((m["servername"], m["name"], m["source"]), []) for m in ps], tree)["category"]
            cl_out.append({"cluster_no": no, "size": len(ps),
                           "category": None if cat is None else cat["path"],
                           "shape": shape.thin_shape(shapes.get(no))})
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

    @r.get("/clusters/{cluster_no}")
    def get_cluster(cluster_no: int, set_name: str = "dump", run_id: Optional[int] = None,
                    method: str = "skew"):
        run = ctx.current_run(set_name, run_id, method)
        members = store.run_members(int(run["id"]), cluster_no=cluster_no, with_features=True)
        if not members:
            raise KeyError(f"없는 묶음: {cluster_no}")
        tree = store.tree()
        labels = store.labels_by_record(set_name=set_name)
        member_labels = [labels.get((m["servername"], m["name"], m["source"]), []) for m in members]
        stored = ctx.shapes_of(int(run["id"])).get(cluster_no)
        return {
            "run_id": int(run["id"]), "method": method, "cluster_no": cluster_no,
            "scan_enum": members[0]["scan_enum"],
            "chuck_id": members[0]["chuck_id"], "sensor_id": members[0]["sensor_id"], "size": len(members),
            # 평균 그림 — 판과 함께 저장된 것, 0009 전의 판이면 여기서 낸다(SL-DRAW-2).
            "shape": stored or shape.mean_shape(m["features"] for m in members),
            **_category_out(shape.cluster_category(member_labels, tree)),
            "members": [{**_record_ref(m), "score": _r(m["score"]), "representative": m["representative"],
                         "labels": _labels_out(tree, ids),
                         "shape": shape.thin_shape(shape.mean_shape([m["features"]]))}
                        for m, ids in zip(members, member_labels)],
        }

    @r.post("/clusters/{cluster_no}/apply")
    def apply_cluster(cluster_no: int, body: ClusterApplyIn):
        """묶음 구성원에게 라벨을 한 번에 붙인다 — 풀의 스냅샷을 쓰므로 fleet 을 부르지 않는다 (SL-CLU-5)."""
        run = ctx.current_run(body.set_name, body.run_id, body.method)
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

    return r
