# connectors/ — the fleet boundary

**The only place scanlabel talks to fleet (ftpmodule).** One connector today, `fleet.py`, over fleet's public HTTP
API. It owns protocol mechanics and the shape of what comes back; it knows no label, no score, no table.

Dependency rule: `connectors` imports the standard library and `httpx`; it must never import `processing`,
`data` or `application` (enforced by `tests/test_architecture.py::test_connectors_know_nothing_of_the_other_areas`).
It never imports fleet's code and never reads fleet's database (`SL-FLEET-1`).

Litmus test: a threshold, a label or a SQL word in `fleet.py` is a violation; so is a second route into fleet
(a new need fleet's API does not serve is a request to fleet, not a shortcut).

## Per-file responsibilities

### `fleet.py` — `Fleet` (HTTP) and `FileCache` (the short per-file memo)
- **`Fleet(base_url, *, transport=None)`** — `GET /servers` (cached briefly; `servername(server_id)` is the
  equipment identity, `SL-FLEET-5`) · `GET /generations` (`generations(set, after_id, limit)`, the cursor `jobs
  index` follows) · `POST /servers/{id}/items/{set}` with `source: "cache"` (`load_file` — fleet answers from
  generations it already parsed and **never opens FTP for us**, `SL-FLEET-2`). `ping` for `/health`.
- **Errors:** `FleetUnavailable` (unreachable → API 502) · `FleetError` (fleet answered with a status; carries it)
  · `NotCached` (a file fleet has not parsed yet → 422, `SL-FLEET-3`; subclass of `ValueError`).
- **Records are told apart by structure** (`SL-FLEET-4`): `pair_scans` pairs a raw scan (`xyz` + `value`) with
  the meta whose `pair` names its `source`, never by `kind`. `Scan` / `FileScans` (`find(source)`) are the result;
  `meta_summary` is the display copy that is stored beside a feature snapshot.
- **`FileCache(fleet, *, size=8, ttl=120.0)`** — one file's records for 120 s so switching records on screen does not
  re-download the file (`SL-FLEET-6`); `get(..., fresh=True)` always re-reads — saving a label stores the `sig`
  that is on screen. `time.monotonic()` ages the memo; it is an interval, not a date, so it is not a wall-clock
  read (`test_wall_clock_reads_live_only_in_timebase` looks only for dates).

## Test map
`tests/conftest.py::FakeFleet` stands in for fleet's three routes through `httpx.MockTransport`, so every API
and job test runs without a fleet. `tests/test_api.py` (`SL-FLEET-2` / `-3` round trips, `test_fleet_down_is_502`)
and `tests/test_cluster.py` (`jobs index` cursor) exercise this area.
