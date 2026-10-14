"""번호 붙은 평문 SQL 마이그레이션 실행기 — fleet 의 `fleet/data/migrate.py` 와 같은 규칙.

순서 · 원장 · 체크섬 · 동시 실행 잠금, 그 넷뿐이다. 전진 전용이고 적용된 파일은 불변이다
(체크섬 불일치는 하드 에러). 파일 하나 = 트랜잭션 하나. Store 는 확인만 하고 거부한다.
코드는 공유하지 않는다 — 두 프로젝트는 서로를 import 하지 않는다 (SPEC.md 「경계」).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import tuple_row

from . import timebase

_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

# fleet 의 잠금 키(0x666C_6565_7401)와 겹치지 않게 — 같은 인스턴스에 DB 만 다르게 둘 수 있다.
_LOCK_KEY = 0x7363_616E_6C01

DSN_ENV = "SCANLABEL_DB_DSN"

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    name        text    NOT NULL,
    checksum    text    NOT NULL,
    applied_at  text    NOT NULL,
    duration_ms integer NOT NULL
);
"""


class MigrationError(RuntimeError):
    """마이그레이션 파일 · 원장의 문제."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


def resolve_dsn(dsn: Optional[str]) -> str:
    raw = dsn if dsn is not None else os.environ.get(DSN_ENV, "")
    if not raw.strip():
        raise MigrationError(
            f"PostgreSQL DSN 이 필요하다: {DSN_ENV}=postgresql://사용자@호스트:포트/scanlabel "
            "(또는 --dsn)")
    return raw.strip()


def discover(directory: Path = _DIR) -> list[Migration]:
    found: dict[int, Migration] = {}
    for p in sorted(directory.glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise MigrationError(f"파일명 규칙 위반 (NNNN_snake_case.sql): {p.name}")
        version = int(m.group(1))
        if version in found:
            raise MigrationError(f"번호 중복: {found[version].path.name} vs {p.name}")
        raw = p.read_bytes()
        found[version] = Migration(version, m.group(2), p, raw.decode("utf-8"),
                                   hashlib.sha256(raw).hexdigest())
    return [found[v] for v in sorted(found)]


def latest_version(directory: Path = _DIR) -> int:
    found = discover(directory)
    return found[-1].version if found else 0


def _ledger(conn) -> dict[int, tuple[str, str]]:
    conn.execute(_LEDGER)
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version")}


def _verify_immutable(found: list[Migration], applied: dict[int, tuple[str, str]]) -> None:
    by_version = {m.version: m for m in found}
    for version, (name, checksum) in sorted(applied.items()):
        m = by_version.get(version)
        if m is None:
            raise MigrationError(f"DB 에 적용된 {version:04d}_{name} 이 코드에 없다 — 코드가 DB 보다 옛것이다")
        if m.checksum != checksum:
            raise MigrationError(
                f"{m.path.name} 이 적용된 뒤 수정됐다 (checksum 불일치). 고치지 말고 새 번호를 추가한다")


def up(dsn: str, *, directory: Path = _DIR) -> list[int]:
    """미적용분을 순서대로 적용한다 (멱등). 적용한 번호 목록."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            found = discover(directory)
            applied = _ledger(conn)
            _verify_immutable(found, applied)
            done: list[int] = []
            for m in found:
                if m.version in applied:
                    continue
                started = timebase.now()
                with conn.transaction():
                    conn.execute(m.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum, applied_at, duration_ms)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (m.version, m.name, m.checksum, timebase.now_iso(),
                         int((timebase.now() - started).total_seconds() * 1000)))
                done.append(m.version)
            return done
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))


def applied_version(conn) -> int:
    """DB 원장의 최신 번호 (원장이 없으면 0). Store 가 기동할 때 코드와 비교한다."""
    # 연결의 행 모양(Store 는 dict_row)에 기대지 않게 tuple 커서로 읽는다.
    with conn.cursor(row_factory=tuple_row) as cur:
        if not cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]:
            return 0
        return int(cur.execute("SELECT coalesce(max(version), 0) FROM schema_migrations").fetchone()[0])


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scanlabel.migrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "status"):
        p = sub.add_parser(name)
        p.add_argument("--dsn", default=None, help=f"기본은 ${DSN_ENV}")
    args = ap.parse_args(argv)
    try:
        dsn = resolve_dsn(args.dsn)
        if args.cmd == "status":
            with psycopg.connect(dsn, autocommit=True) as conn:
                applied = _ledger(conn)
            found = discover()
            _verify_immutable(found, applied)
            todo = [m for m in found if m.version not in applied]
            for v, (n, _) in sorted(applied.items()):
                print(f"  applied  {v:04d}_{n}")
            for m in todo:
                print(f"  PENDING  {m.version:04d}_{m.name}")
            return 1 if todo else 0
        done = up(dsn)
        print(f"적용: {len(done)}건" + (f" ({', '.join(f'{v:04d}' for v in done)})" if done else ""))
        return 0
    except MigrationError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
