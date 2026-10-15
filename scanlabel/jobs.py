"""CLI — `python -m scanlabel.jobs {seed-labels,eval,refeature,index,cluster}`.

- `seed-labels FILE` — 대 › 중 › 소 라벨 트리를 넣는다(JSON 또는 CSV). **멱등**: 같은 부모 아래
  같은 이름이 이미 있으면 그 노드를 쓰고(코드 · 설명 · 사례가 비어 있으면 채운다), 없는 것만 만든다.
- `eval` — leave-one-out 단계별 적중률 (SPEC.md 「측정」). 스냅샷만 쓰므로 fleet 이 필요 없다.
- `refeature` — `FEATURE_VERSION` 이 다른 라벨의 특징을 fleet 캐시에서 다시 만든다. 그 세대가
  fleet 에 없으면 `stale` 로 보고하고 건너뛴다.
- `index` — fleet 이 해석해 둔 파일을 기록 풀에 넣는다(`/generations` 커서, FTP 없음). 주기 실행용.
- `cluster` — 기록 풀을 모양으로 다시 묶어 저장한다(화면의 「다시 묶기」와 같은 일).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import shape
from .fleet_client import FileScans, Fleet, FleetError, FleetUnavailable, NotCached, Scan, meta_summary
from .store import Store
from .tree import LEVEL_NAMES


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
                    "scan_enum": str(meta.get("scan_enum") or ""), "meta": meta,
                    "features": shape.record_features(s.scan), "feature_version": shape.FEATURE_VERSION})
    return out


def index(store: Store, fleet: Fleet, *, set_name: str, page: int = 500,
          max_files: Optional[int] = None) -> dict:
    """fleet 이 해석해 둔 파일을 풀에 넣는다 — `/generations` 를 커서로 따라가며 (SL-POOL-2).

    커서(`index_cursors`)는 다음 실행이 이어 읽을 자리다. 한 파일의 여러 세대가 한 페이지에
    오면 최신 하나만 읽는다(이름으로 읽으면 언제나 최신 세대다). fleet 캐시에서 이미 사라진
    파일은 `skipped` 로 남기고 넘어간다 — FTP 는 일으키지 않는다.
    """
    after = store.cursor(set_name)
    files = records = 0
    skipped: list[dict] = []
    while True:
        rows, nxt = fleet.generations(set_name, after, limit=page)
        if not rows:
            break
        seen: dict[tuple[int, str], dict] = {}
        for r in rows:
            seen[(int(r["server_id"]), str(r["name"]))] = r
        for (server_id, name) in seen:
            if max_files is not None and files >= max_files:
                break
            try:
                f = fleet.load_file(server_id, set_name, name)
            except (NotCached, FleetError) as exc:
                skipped.append({"server_id": server_id, "name": name, "why": str(exc)})
                continue
            records += store.upsert_records(pool_rows(f))
            files += 1
        if max_files is not None and files >= max_files:
            break                                  # 커서는 다 읽은 페이지까지만 — 남은 것은 다음 실행이
        after = int(nxt if nxt is not None else rows[-1]["id"])
        store.set_cursor(set_name, after)
    return {"files": files, "records": records, "skipped": skipped, "cursor": after}


def run_clusters(store: Store, *, set_name: str) -> dict:
    """풀 전체를 다시 묶어 저장한다 (SL-CLU-1). 반환 = 저장된 결과의 요약."""
    rows, total, truncated, seq = store.pool(set_name=set_name, per_scan_limit=shape.CLUSTER_MAX_N)
    result = shape.cluster(rows)
    run_id = store.save_cluster_run(
        set_name=set_name, result=result, n_records=len(rows), truncated=truncated, pool_seq=seq,
        threshold=shape.CLUSTER_MIN, min_size=shape.CLUSTER_MIN_SIZE,
        feature_version=shape.FEATURE_VERSION)
    return {"run_id": run_id, "records": len(rows), "pool": total, "truncated": truncated,
            "clusters": len(result["clusters"]), "unclustered": len(result["unclustered"])}


def _print_eval(res: dict) -> None:
    print(f"라벨된 기록 {res['labelled']}건 — leave-one-out")
    print(f"  {'단계':<6} {'n':>4} {'top-1':>8} {'top-3':>8} {'제안':>6} {'제안 적중':>9}")
    for level, o in res["levels"].items():
        n = o["n"]

        def pct(x: int, d: int) -> str:
            return f"{x / d * 100:5.1f}%" if d else "    -"
        print(f"  {LEVEL_NAMES[level]:<6} {n:>4} {pct(o['top1'], n):>8} {pct(o['top3'], n):>8}"
              f" {o['suggested']:>6} {pct(o['suggested_right'], o['suggested']):>9}")


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scanlabel.jobs")
    ap.add_argument("--dsn", default=None, help="기본은 $SCANLABEL_DB_DSN")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed-labels", help="라벨 트리 넣기 (JSON | CSV, 멱등)")
    p.add_argument("file", type=Path)
    p = sub.add_parser("eval", help="leave-one-out 단계별 적중률")
    p.add_argument("--set-name", default="dump")
    p.add_argument("--all-scans", action="store_true", help="스캔 종류가 달라도 비교")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("refeature", help="FEATURE_VERSION 이 다른 라벨의 특징 다시 만들기")
    p.add_argument("--set-name", default="dump")
    p.add_argument("--fleet-url", default=os.environ.get("FLEET_API_URL", "http://localhost:8000"))
    p = sub.add_parser("index", help="fleet 이 해석해 둔 파일을 기록 풀에 넣기 (커서, FTP 없음)")
    p.add_argument("--set-name", default="dump")
    p.add_argument("--fleet-url", default=os.environ.get("FLEET_API_URL", "http://localhost:8000"))
    p.add_argument("--max-files", type=int, default=None, help="이번 실행에서 읽을 파일 수 상한")
    p = sub.add_parser("cluster", help="기록 풀을 모양으로 다시 묶기")
    p.add_argument("--set-name", default="dump")
    args = ap.parse_args(argv)

    dsn = args.dsn or os.environ.get("SCANLABEL_DB_DSN", "")
    if not dsn:
        print("error: SCANLABEL_DB_DSN 이 필요하다")
        return 2
    store = Store(dsn)
    try:
        if args.cmd == "seed-labels":
            print(json.dumps(seed_labels(store, args.file), ensure_ascii=False))
        elif args.cmd == "eval":
            res = run_eval(store, set_name=args.set_name, same_scan_only=not args.all_scans)
            print(json.dumps(res, ensure_ascii=False, indent=2)) if args.json else _print_eval(res)
        elif args.cmd == "cluster":
            print(json.dumps(run_clusters(store, set_name=args.set_name), ensure_ascii=False, indent=2))
        else:
            fleet = Fleet(args.fleet_url)
            try:
                res = (refeature(store, fleet, set_name=args.set_name) if args.cmd == "refeature"
                       else index(store, fleet, set_name=args.set_name, max_files=args.max_files))
                print(json.dumps(res, ensure_ascii=False, indent=2))
            except FleetUnavailable as exc:
                print(f"error: {exc}")
                return 2
            finally:
                fleet.close()
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
