"""테스트 배선 — 실제 PostgreSQL(`SCANLABEL_TEST_DSN`)과 가짜 fleet(httpx.MockTransport).

DB 는 세션에 한 번 마이그레이션하고, DB 를 쓰는 테스트마다 TRUNCATE … RESTART IDENTITY 로
비운다(fleet 의 tests/pgtest.py 와 같은 방식). DSN 이 없으면 **조용히 건너뛰지 않고** 실패한다.
"""
from __future__ import annotations

import json
import os

import httpx
import psycopg
import pytest

from scanlabel.data import migrate

_ENV = "SCANLABEL_TEST_DSN"


@pytest.fixture(scope="session")
def _migrated() -> str:
    dsn = os.environ.get(_ENV, "").strip()
    if not dsn:
        raise RuntimeError(f"{_ENV} 가 필요하다 — 예: postgresql://postgres@127.0.0.1:5433/scanlabel_test")
    migrate.up(dsn)
    return dsn


@pytest.fixture
def dsn(_migrated: str) -> str:
    with psycopg.connect(_migrated, autocommit=True) as c:
        names = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'")]
        if names:
            c.execute("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE")
    return _migrated


class FakeFleet:
    """fleet 의 세 라우트만 흉내 낸다 — GET /servers, POST /servers/{id}/items/dump, GET /generations."""

    def __init__(self):
        self.servers = [{"id": 1, "host": "10.0.0.1", "servername": "scanner-a"},
                        {"id": 2, "host": "10.0.0.2", "servername": "scanner-b"}]
        self.files: dict[tuple[int, str], dict] = {}     # (server_id, name) → {sig, records}
        self.gens: list[dict] = []                        # /generations 의 행 (도착 순서)
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False

    def put(self, server_id: int, name: str, records: list, sig: str = "20260811T005720-100"):
        self.files[(server_id, name)] = {"sig": sig, "records": records}
        self.gens.append({"id": len(self.gens) + 1, "server_id": server_id, "set_name": "dump",
                          "name": name, "sig": sig})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/servers":
            return httpx.Response(200, json=self.servers)
        if request.method == "GET" and request.url.path == "/generations":
            after = int(request.url.params["after_id"])
            limit = int(request.url.params.get("limit", 200))
            rows = [g for g in self.gens if g["id"] > after][:limit]
            return httpx.Response(200, json={"generations": rows,
                                             "next_after_id": rows[-1]["id"] if rows else None})
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and len(parts) == 4 and parts[0] == "servers" and parts[2] == "items":
            sid = int(parts[1])
            if sid not in {s["id"] for s in self.servers}:
                return httpx.Response(404, json={"detail": "unknown server"})
            name = body["files"][0]
            hit = self.files.get((sid, name))
            if hit is None:
                return httpx.Response(200, json={
                    "item": "dump", "records": [], "served": [],
                    "issues": [{"file": name, "where": "assemble", "reason": "no cache"}]})
            return httpx.Response(200, json={
                "item": "dump", "records": hit["records"], "issues": [],
                "served": [{"file": name, "sig": hit["sig"], "mtime": "2026-08-11T00:57:20+09:00"}]})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def fleet() -> FakeFleet:
    return FakeFleet()


@pytest.fixture
def client(dsn: str, fleet: FakeFleet):
    from fastapi.testclient import TestClient

    from scanlabel.application.api import create_app

    app = create_app(dsn, "http://fleet.test", fleet_transport=httpx.MockTransport(fleet.handler))
    with TestClient(app) as c:
        yield c
