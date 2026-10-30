"""CLI — `python -m scanlabel.application.jobs {seed-labels,eval,refeature,index,cluster}`.

- `seed-labels FILE` — 대 › 중 › 소 라벨 트리를 넣는다(JSON 또는 CSV). **멱등**: 같은 부모 아래
  같은 이름이 이미 있으면 그 노드를 쓰고(코드 · 설명 · 사례가 비어 있으면 채운다), 없는 것만 만든다.
- `eval` — leave-one-out 단계별 적중률 (SPEC.md 「측정」). 스냅샷만 쓰므로 fleet 이 필요 없다.
- `refeature` — `FEATURE_VERSION` 이 다른 라벨의 특징을 fleet 캐시에서 다시 만든다. 그 세대가
  fleet 에 없으면 `stale` 로 보고하고 건너뛴다.
- `index` — fleet 이 해석해 둔 파일을 기록 풀에 넣는다(`/generations` 커서, FTP 없음). 주기 실행용.
- `cluster` — 기록 풀을 두 잣대(채널 간 편차 · 주성분)로 다시 묶어 저장한다(화면의 「다시 묶기」와 같은 일).
  `--method skew|pca` 로 하나만. 라벨된 기록으로 잰 순도 · 덮음과 두 잣대의 일치도(adjusted Rand)를 찍는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from ..connectors.fleet import Fleet, FleetUnavailable
from ..data.repository import Store
from ..processing import shape
from ..processing.tree import LEVEL_NAMES
from .usecases import index, refeature, run_clusters, run_eval, seed_labels


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
    ap = argparse.ArgumentParser(prog="python -m scanlabel.application.jobs")
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
    p = sub.add_parser("cluster", help="기록 풀을 다시 묶기 — 채널 간 편차 · 주성분 두 잣대 (라벨로 잰 순도 · 일치도)")
    p.add_argument("--set-name", default="dump")
    p.add_argument("--method", choices=("all", "skew", "pca"), default="all", help="묶을 잣대 (기본: 둘 다)")
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
            methods = shape.METHODS if args.method == "all" else (args.method,)
            print(json.dumps(run_clusters(store, set_name=args.set_name, methods=methods),
                             ensure_ascii=False, indent=2))
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
