"""fleet 을 부르는 유일한 곳 (SPEC.md 「경계」).

scanlabel 은 fleet 의 **공개 HTTP API** 만 쓴다 — fleet 의 DB 를 읽지 않고, 코드를 import 하지
않는다. 아이템 실행은 항상 `source: "cache"` 다: fleet 이 이미 받아 해석해 둔 세대만 읽고
**FTP 를 한 번도 일으키지 않는다**(fleet README.api.md §2.1). 응답 계약은
`ftpmodule/fleet/processing/parse/dump/interface.md`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx


class FleetUnavailable(RuntimeError):
    """fleet 에 닿지 않았다 (접속 · 시간 초과) → 502."""


class FleetError(RuntimeError):
    """fleet 이 답했지만 실패였다. status 는 그대로 옮긴다(404 = 모르는 서버 등)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class NotCached(ValueError):
    """fleet 에 그 파일의 해석 결과가 없다 → 422 「먼저 Dump 화면에서 파일을 열어 달라」."""


@dataclass
class Scan:
    source: str
    scan: dict                 # raw 스캔 레코드 (value · xyz)
    meta: Optional[dict]       # 짝 메타 (pair 가 이 source 를 가리키는 것)


@dataclass
class FileScans:
    server_id: int
    servername: str
    set_name: str
    name: str
    sig: str
    scans: list[Scan] = field(default_factory=list)

    def find(self, source: str) -> Scan:
        for s in self.scans:
            if s.source == source:
                return s
        raise KeyError(f"이 파일에 그 기록이 없다: {source}")


def pair_scans(records: list) -> list[Scan]:
    """구조로 가른다 — `xyz` 가 있으면 raw, `results` 가 있으면 메타 (kind 문자열에 의존 금지)."""
    raws = [r for r in records if isinstance(r, dict) and isinstance(r.get("xyz"), dict)
            and isinstance(r.get("value"), dict)]
    metas = [r for r in records if isinstance(r, dict) and isinstance(r.get("results"), list)]
    by_pair = {m.get("pair"): m for m in metas if m.get("pair")}
    return [Scan(str(r.get("source", "")), r, by_pair.get(r.get("source"))) for r in raws]


def meta_summary(meta: Optional[dict]) -> dict:
    """라벨 행에 남길 표시용 메타 사본 — 원본 캐시가 지워져도 "어느 척 · 센서였나"는 남는다."""
    if not meta:
        return {}
    keep = ("scan_enum", "chuck_id", "sensor_id", "scan_queued", "timestamp")
    return {k: meta.get(k) for k in keep if meta.get(k) is not None}


class Fleet:
    def __init__(self, base_url: str, *, transport: Optional[httpx.BaseTransport] = None,
                 timeout: float = 60.0, servers_ttl: float = 60.0):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=timeout)
        self._servers_ttl = servers_ttl
        self._servers: tuple[float, dict[int, str]] = (0.0, {})
        self._lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            res = self._http.request(method, path, **kw)
        except httpx.TransportError as exc:
            raise FleetUnavailable(f"fleet 에 닿지 않는다 ({self._http.base_url}): {exc}") from exc
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail", res.text)
            except ValueError:
                detail = res.text
            raise FleetError(res.status_code, f"fleet {method} {path} → {res.status_code}: {detail}")
        return res

    def ping(self) -> bool:
        self._call("GET", "/servers")
        return True

    def servername(self, server_id: int) -> str:
        """fleet 서버 id → servername(장비 정체성). 짧게 캐시한다."""
        with self._lock:
            at, table = self._servers
            if server_id in table and time.monotonic() - at < self._servers_ttl:
                return table[server_id]
        rows = self._call("GET", "/servers").json()
        table = {int(r["id"]): str(r.get("servername") or r.get("host") or r["id"]) for r in rows}
        with self._lock:
            self._servers = (time.monotonic(), table)
        if server_id not in table:
            raise KeyError(f"fleet 에 없는 서버 id: {server_id}")
        return table[server_id]

    def load_file(self, server_id: int, set_name: str, name: str) -> FileScans:
        """그 파일의 최신 해석 세대 — 캐시에서만. 없으면 NotCached."""
        servername = self.servername(server_id)
        body = self._call("POST", f"/servers/{server_id}/items/{set_name}",
                          json={"files": [name], "source": "cache"}).json()
        records = body.get("records") or []
        served = [s for s in body.get("served") or [] if s.get("file") == name]
        if not records and not served:
            raise NotCached(f"fleet 에 {name} 의 해석 결과가 없다 — 먼저 Dump 화면에서 파일을 열어 달라")
        return FileScans(server_id, servername, set_name, name,
                         str(served[0].get("sig") or "") if served else "",
                         pair_scans(records))


class FileCache:
    """파일 단위 짧은 캐시 — 기록을 바꿀 때마다 fleet 에서 파일 전체를 다시 받지 않게.

    키는 (server_id, set_name, name). 세대가 바뀌어도 `ttl` 안에서는 직전 것을 쓴다 —
    라벨 저장(PUT)은 `fresh=True` 로 항상 새로 읽어 그 시점의 세대를 남긴다.
    """

    def __init__(self, fleet: Fleet, *, size: int = 8, ttl: float = 120.0):
        self.fleet = fleet
        self.size, self.ttl = size, ttl
        self._items: dict[tuple, tuple[float, FileScans]] = {}
        self._lock = threading.Lock()

    def get(self, server_id: int, set_name: str, name: str, *, fresh: bool = False) -> FileScans:
        key = (server_id, set_name, name)
        with self._lock:
            hit = self._items.get(key)
            if hit and not fresh and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
        got = self.fleet.load_file(server_id, set_name, name)
        with self._lock:
            self._items[key] = (time.monotonic(), got)
            while len(self._items) > self.size:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                del self._items[oldest]
        return got
