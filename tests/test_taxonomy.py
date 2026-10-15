"""실제 분류표 ID-0603 — 마이그레이션 0003 이 넣는 트리 (SPEC.md SL-TREE-11 · SL-TREE-12)."""
from __future__ import annotations

from pathlib import Path

import psycopg

from scanlabel.store import Store

SQL = Path(__file__).resolve().parents[1] / "scanlabel" / "migrations" / "0003_taxonomy_id_0603.sql"


def apply(dsn: str) -> None:
    # 테스트 픽스처가 표를 비우므로 파일을 직접 다시 돌린다 — 마이그레이션처럼 한 트랜잭션으로.
    with psycopg.connect(dsn) as c:
        c.execute(SQL.read_text(encoding="utf-8"))


def test_taxonomy_0603_is_a_fixed_three_level_tree_and_idempotent(dsn):
    apply(dsn)
    apply(dsn)                                               # 두 번 넣어도 늘지 않는다
    store = Store(dsn)
    try:
        defs = store.list_defs()
    finally:
        store.close()
    by_level = {lv: [d for d in defs if d["level"] == lv] for lv in (1, 2, 3)}
    assert [d["name"] for d in by_level[1]] == ["ID-0603"]
    assert len(by_level[2]) == 9 and len(by_level[3]) == 41
    paths = {d["path"] for d in defs}
    # 이름 안의 묶음 접두어는 4단계가 되지 않는다 — 소분류 이름 그대로
    assert "ID-0603 › WS › IFM-MIOB" in paths
    assert "ID-0603 › C&T › Air shower-air balance" in paths
    # error 가 빈 행은 중분류에서 끝나는 가지
    sw = next(d for d in defs if d["name"] == "SW/Firmware")
    assert sw["level"] == 2 and not [d for d in defs if d["parent_id"] == sw["id"]]
    # 파일 순서를 지킨다
    assert [d["name"] for d in by_level[2]][:3] == ["dynamic", "RS", "WS"]
    assert all(d["origin"] == "seed" and d["created_at"].endswith("+09:00") for d in defs)
