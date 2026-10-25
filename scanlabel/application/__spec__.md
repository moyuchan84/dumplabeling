# application/ — use cases and their two adapters

**What a caller can ask for.** `usecases.py` is the work; `api.py` (HTTP) and `jobs.py` (argv) are thin adapters
over it. This is the one area allowed to compose `processing`, `data` and `connectors`.

Dependency rule: `application` may import every other area; nothing imports `application`. `api.py` and
`jobs.py` never import each other — both call `usecases.py` (enforced by
`tests/test_architecture.py::test_the_thin_adapters_do_not_call_each_other`).

New requirements are covered here and in `processing/` (the judgement); a requirement-shaped noun must not leak
into `connectors/` or `data/`.

## Per-file responsibilities

### `usecases.py` — functions that know neither HTTP nor argv
- `seed_labels(store, path)` (idempotent; JSON or `대분류,중분류,소분류[,코드,설명,사례번호]` CSV, `SL-TREE-8`) ·
  `run_eval` (leave-one-out, snapshots only — no fleet) · `refeature` (rebuild stale snapshots from fleet's cache,
  labelled records and the pool alike) · `pool_rows` (a file's records → pool rows) · `index` (follow fleet's
  `/generations` cursor, cache only, `SL-POOL-2`) · `run_clusters` (cluster + map the pool, store the run).
- They take a `Store` / `Fleet` as arguments — nothing here constructs a connection, so a test hands in a fake.

### `api.py` — `create_app(dsn, fleet_url)`
- Routes: `/health` · `/label-defs` · `/labels` (+ `/labels/similar`, `/labels/by-case/{id}`) · `/clusters` (+
  `/run`, `/map`, `/{no}/apply`) · `/records/{id}`. The caller contract is `README.api.md`; change a route, its
  response or an error there in the same change.
- Error promise: `ValueError` → 422 · `KeyError` → 404 · `NotCached` → 422 · `FleetUnavailable` → 502 ·
  `FleetError` → its own status. `POST /clusters/run` answers 202 and runs in a background thread (`SL-CLU-8`).
- `uvicorn scanlabel.application.api:app` — `app` is built lazily (module `__getattr__`) so importing the module
  does not open a connection.
- **Judgements stay server-side** (`SL-ROLE-3`): a score, a threshold or "is this suggested" is computed in
  `processing/` and only transcribed by the front end.

### `jobs.py` — the CLI
- `python -m scanlabel.application.jobs {seed-labels, eval, refeature, index, cluster}`. Arguments, printing and
  exit codes only; each subcommand is one `usecases` call. Anything scheduled is a `jobs` subcommand.

## Decisions

### `usecases.py` split out of `jobs.py` (2026-10-19)
- **Decision:** `pool_rows` / `run_clusters` and the other work functions live in `usecases.py`; `jobs.py` is the
  argv adapter only.
- **Reason:** `api.py` imported `jobs.py` for `pool_rows` / `run_clusters`, so an HTTP route depended on a CLI
  module and `jobs.py`'s `argparse` / `sys` came along.
- **Rejected:** leaving them in `jobs.py` and documenting the exception (the dependency would stay invisible to
  the architecture test); duplicating them in `api.py` (two definitions of the pool row).
- **Revisit when:** a third adapter appears (a scheduler thread, a message consumer) — it would call `usecases`
  the same way.
- **Validation:** `test_the_thin_adapters_do_not_call_each_other`.

## Test map
`tests/test_api.py` — labels, tree, seed, eval, suggestions, the 4xx/5xx promises. `tests/test_cluster.py` —
index, clusters, apply, map, the background re-run.
