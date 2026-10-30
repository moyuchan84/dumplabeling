"""유스케이스 — API 와 CLI 가 함께 쓰는 일 (라벨 트리 넣기 · 평가 · 특징 재생성 · 풀 색인 · 묶기).

HTTP 도 argv 도 모르는 함수들이다: 얇은 어댑터(`api.py` · `jobs.py`)가 이것을 부른다. 순수 계산은
`processing/`, 저장은 `data/`, fleet 읽기는 `connectors/` 에 있고, 여기서는 그 셋을 엮기만 한다.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..connectors.fleet import FileScans, Fleet, FleetError, NotCached, Scan, meta_summary
from ..data.repository import Store
from ..processing import shape


def _iter_json(nodes: list, parents: tuple = ()) -> Iterator[tuple[tuple, dict]]:
    """[{name, code?, description?, case_id?, children: [...]}] → (조상 이름들, 노드)."""
    for n in nodes:
        yield parents, n
        yield from _iter_json(n.get("children") or [], parents + (n["name"],))


def _iter_csv(path: Path) -> Iterator[tuple[tuple, dict]]:
    """열: 대분류, 중분류, 소분류[, 코드, 설명, 사례번호] — 비운 칸 아래는 만들지 않는다.

    코드 · 설명 · 사례번호는 그 행의 **가장 깊은** 노드에 붙는다. 같은 대분류가 여러 행에
    나와도 한 번만 만들어진다.
    """
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if rows and rows[0] and rows[0][0].strip() in ("대분류", "level1", "l1"):
        rows = rows[1:]
    for r in rows:
        r = [c.strip() for c in r] + [""] * 6
        names = [n for n in r[:3]]
        depth = 0
        while depth < 3 and names[depth]:
            depth += 1
        for d in range(depth):
            node = {"name": names[d]}
            if d == depth - 1:
                node.update(code=r[3], description=r[4], case_id=int(r[5]) if r[5] else None)
            yield tuple(names[:d]), node


def seed_labels(store: Store, path: Path) -> dict:
    if path.suffix.lower() == ".csv":
        items = list(_iter_csv(path))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = list(_iter_json(data["labels"] if isinstance(data, dict) else data))
    ids: dict[tuple, int] = {}
    made = kept = 0
    for order, (parents, node) in enumerate(items):
        parent_id = ids[parents] if parents else None
        key = parents + (node["name"],)
        if key in ids:
            continue
        found = store.find_child(parent_id, node["name"])
        if found:
            ids[key] = int(found["id"])
            fill = {k: node[k] for k in ("code", "description", "case_id")
                    if node.get(k) and not found.get(k)}
            if fill:
                store.update_def(int(found["id"]), fill)
            kept += 1
            continue
        row = store.create_def(parent_id=parent_id, name=node["name"], code=node.get("code") or "",
                               description=node.get("description") or "",
                               case_id=node.get("case_id"), origin="seed", sort_no=order)
        ids[key] = int(row["id"])
        made += 1
    return {"created": made, "existing": kept}


def run_eval(store: Store, *, set_name: str, same_scan_only: bool) -> dict:
    labelled = store.candidates(set_name=set_name)
    return {"labelled": len(labelled),
            "levels": shape.evaluate(labelled, store.tree(), same_scan_only=same_scan_only)}


def refeature(store: Store, fleet: Fleet, *, set_name: str) -> dict:
    done, stale = 0, []
    for c in store.candidates(set_name=set_name):
        if c["feature_version"] == shape.FEATURE_VERSION:
            continue
        try:
            f = fleet.load_file(int(c["server_id"]), set_name, c["name"])
            scan = f.find(c["source"])
        except (NotCached, KeyError, FleetError) as exc:
            stale.append({"id": c["id"], "name": c["name"], "source": c["source"], "why": str(exc)})
            continue
        store.set_features(int(c["id"]), shape.record_features(scan.scan), shape.FEATURE_VERSION)
        done += 1
    # 풀도 같은 판이어야 묶음이 한 정의로 비교된다 — 파일 단위로 다시 읽는다.
    pooled = 0
    for (server_id, name), sources in store.stale_pool(set_name, shape.FEATURE_VERSION).items():
        try:
            f = fleet.load_file(server_id, set_name, name)
        except (NotCached, FleetError) as exc:
            stale += [{"pool": True, "name": name, "source": s, "why": str(exc)} for s in sources]
            continue
        pooled += store.upsert_records(pool_rows(f, [s for s in f.scans if s.source in sources]))
    return {"refeatured": done, "pool_refeatured": pooled, "stale": stale}


def pool_rows(f: FileScans, scans: Optional[Iterable[Scan]] = None) -> list[dict]:
    """fleet 에서 읽은 파일의 기록들 → 풀(scan_records) 행 (SL-POOL-1)."""
    out = []
    for s in (f.scans if scans is None else scans):
        meta = meta_summary(s.meta)
        out.append({"servername": f.servername, "server_id": f.server_id, "set_name": f.set_name,
                    "name": f.name, "source": s.source, "sig": f.sig,
                    "scan_enum": str(meta.get("scan_enum") or ""),
                    "chuck_id": "" if meta.get("chuck_id") is None else str(meta["chuck_id"]),
                    "sensor_id": "" if meta.get("sensor_id") is None else str(meta["sensor_id"]), "meta": meta,
                    "features": shape.record_features(s.scan), "feature_version": shape.FEATURE_VERSION})
    return out


def index(store: Store, fleet: Fleet, *, set_name: str, page: int = 500,
          max_files: Optional[int] = None) -> dict:
    """fleet 이 해석해 둔 파일을 풀에 넣는다 — `/generations` 를 커서로 따라가며 (SL-POOL-2).

    커서(`index_cursors`)는 다음 실행이 이어 읽을 자리 — **처리한 마지막 세대의 id** 다(읽었든
    건너뛰었든). 이번 실행에서 이미 읽은 파일의 세대가 또 오면 건너뛴다(이름으로 읽으면 언제나
    최신 세대다). fleet 캐시에서 이미 사라진 파일은 `skipped` 로 남기고 넘어간다 — FTP 는 일으키지
    않는다. `max_files` 에 닿으면 그 자리에서 멈추고, 다음 실행이 바로 뒤부터 잇는다.
    """
    after = store.cursor(set_name)
    files = records = 0
    skipped: list[dict] = []
    done: set[tuple[int, str]] = set()
    try:
        while max_files is None or files < max_files:
            rows, _ = fleet.generations(set_name, after, limit=page)
            if not rows:
                break
            for r in rows:
                if max_files is not None and files >= max_files:
                    break
                key = (int(r["server_id"]), str(r["name"]))
                if key not in done:
                    done.add(key)
                    try:
                        f = fleet.load_file(key[0], set_name, key[1])
                    except (NotCached, FleetError) as exc:
                        skipped.append({"server_id": key[0], "name": key[1], "why": str(exc)})
                    else:
                        records += store.upsert_records(pool_rows(f))
                        files += 1
                after = int(r["id"])
    finally:
        store.set_cursor(set_name, after)          # 도중에 실패해도 처리한 데까지는 남긴다
    return {"files": files, "records": records, "skipped": skipped, "cursor": after}


def run_clusters(store: Store, *, set_name: str, methods: Iterable[str] = shape.METHODS) -> dict:
    """풀 전체를 잣대마다 다시 묶어 저장한다 (SL-CLU-1 · SL-CLU-10). 반환 = 잣대별 요약 + 라벨로 잰 숫자.

    풀은 한 번만 읽는다 — 두 잣대가 같은 기록을 묶는다. 주성분 투영식은 범위마다 한 번 내서 묶음과
    지도가 함께 쓴다. `quality` 는 라벨된 기록으로 잰 순도 · 덮음(`shape.cluster_quality`), `agreement` 는
    두 잣대가 얼마나 같게 갈랐나(adjusted Rand) — 실데이터로 두 잣대를 견줄 때 본다.
    """
    methods = list(methods)
    for m in methods:
        shape.check_method(m)
    rows, total, truncated, seq = store.pool(set_name=set_name, per_scan_limit=shape.CLUSTER_MAX_N)
    kinds: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        kinds.setdefault(shape.group_of(r), []).append(r)
    tree = store.tree()
    labels = store.labels_by_record_id(set_name=set_name)
    out: dict = {"records": len(rows), "pool": total, "truncated": truncated, "methods": {}}
    results: dict[str, dict] = {}
    for method in methods:
        models = ({g: shape.pca_model(r["features"] for r in group) for g, group in kinds.items()}
                  if method == "pca" else None)
        result = shape.cluster(rows, method, models=models)
        # 묶음 지도 — 묶음 범위(스캔 종류, 척, 센서)마다 따로 (SL-MAP-1). rows 는 id 순이라 결과가 결정적이다.
        maps: dict[str, dict] = {}
        points: list[tuple] = []
        for group_id, group in kinds.items():
            m = shape.cluster_map(group, method, model=models.get(group_id) if models else None)
            if m["proj"] is not None:
                maps[shape.group_key(*group_id)] = m["proj"]
            points += [(rid, *group_id, x, y) for rid, (x, y) in m["points"].items()]
        run_id = store.save_cluster_run(
            set_name=set_name, result=result, n_records=len(rows), truncated=truncated, pool_seq=seq,
            threshold=shape.CLUSTER_MIN, min_size=shape.CLUSTER_MIN_SIZE,
            feature_version=shape.FEATURE_VERSION, method=method, maps=maps, points=points)
        results[method] = result
        out["methods"][method] = {"run_id": run_id, "clusters": len(result["clusters"]),
                                  "unclustered": len(result["unclustered"]),
                                  "quality": shape.cluster_quality(result, labels, tree)}
    if "skew" in results and "pca" in results:
        out["agreement"] = shape.partition_agreement(results["skew"], results["pca"])
    return out
