# CLAUDE.md — scanlabel working rules

A FastAPI + PostgreSQL service that labels dump records with a three-level tree and suggests labels by
shape similarity. It reads fleet (ftpmodule) through fleet's public HTTP API only. **`SPEC.md` holds every
requirement and decision** — read it before changing behaviour, and edit it in the same change.

## Area map

Four areas under `scanlabel/`, dependencies pointing one way (`tests/test_architecture.py` enforces it):
`processing` ← `data` ← `application`, and `connectors` ← `application`. Per-area detail lives in each area's
`__spec__.md` — **read the owning spec instead of re-analyzing.**

| Area | File | Responsibility |
|---|---|---|
| processing (pure) | `processing/shape.py` | Snapshot features, similarity (+ `prepare` for many comparisons), ranking with the tree roll-up, similar cases, clustering and a cluster's category, the cluster map, leave-one-out evaluation. Every threshold and weight lives here, as a constant. Stdlib only — knows no DB, no HTTP, no clock |
| processing (pure) | `processing/tree.py` | The label tree's rules: path, ancestors, effective retirement, selection check, level of a new node, move check |
| connectors | `connectors/fleet.py` | The only place that calls fleet — `GET /servers`, `GET /generations`, `POST /servers/{id}/items/{set}` with `source: "cache"`; structural pairing of raw scans and metas; the short per-file cache. Knows no label, score or table |
| data | `data/repository.py` | PostgreSQL `Store` — raw SQL over a psycopg pool: label tree, record labels, the record pool (`scan_records`), the index cursor, cluster runs and map points. `ValueError` → 422, `KeyError` → 404. Verifies the schema version at start and refuses on a mismatch |
| data | `data/migrate.py` + `data/migrations/` | Numbered forward-only SQL, immutable once applied (checksum), one transaction per file. `python -m scanlabel.data.migrate {up,status}` |
| application | `application/usecases.py` | The work both adapters share: `seed_labels` · `run_eval` · `refeature` · `pool_rows` · `index` · `run_clusters`. Knows neither HTTP nor argv |
| application | `application/api.py` | FastAPI routes (`create_app`) — contract in `README.api.md`. Thin: calls `usecases` / `Store` / `processing` |
| application | `application/jobs.py` | CLI — `seed-labels` · `eval` · `refeature` · `index` · `cluster`. Arguments and printing only. Never imported by `api.py` |
| top level | `timebase.py` | The **only** wall-clock reads + KST conversions |

## Change → what to touch

| Change | Where (code · docs · tests) |
|---|---|
| A threshold / weight / similarity rule | `processing/shape.py` constant · `SPEC.md` line · `tests/test_shape.py` — **run `jobs eval` on the real labels and put the numbers in the commit message** |
| The snapshot's shape | `processing/shape.py` + bump `FEATURE_VERSION` · `jobs refeature` after deploying · `SPEC.md` `SL-FEAT` |
| A tree rule | `processing/tree.py` (+ the DB `CHECK` in a new migration if it must hold in SQL) · `tests/test_tree.py` |
| Endpoint / response / error | `application/api.py` (+ `usecases.py` when the work is shared) · `README.api.md` in the same change · `tests/test_api.py` |
| A new job / CLI subcommand | the function in `application/usecases.py` · a thin subcommand in `jobs.py` · `README.md` |
| Schema | new `data/migrations/NNNN_*.sql` + `Store` methods · `data/__spec__.md` table map · `tests/test_api.py` / `tests/test_cluster.py` |
| A new need from fleet | a request to fleet first (`SL-FLEET-1`); then `connectors/fleet.py` only |
| A settled decision | `SPEC.md` (repo-wide) or the owning area's `__spec__.md` — Decision / Reason / Rejected / Revisit when / Validation |

Which tests to run for any edit: `README.dev.md` §「선택 테스트 지도」.

## Rules

- **Never import fleet, never read its database.** A need fleet's API does not serve is a request to fleet,
  not a shortcut (`SL-FLEET-1`).
- **Never trigger FTP** — every item run is `source: "cache"` (`SL-FLEET-2`).
- Judgements stay here; poc_viewer transcribes them (`SL-ROLE-3`). Do not add a threshold to the front end.
- Changing a constant in `processing/shape.py` is a behaviour change: run `jobs eval` on the real labels and write the
  numbers in the commit message.
- Changing the snapshot's shape bumps `FEATURE_VERSION`; run `jobs refeature` after deploying.
- Schema: a new `data/migrations/NNNN_*.sql`, never an edit to an applied one.
- Dependencies point down only; a new module goes in an area, not at the top (`tests/test_architecture.py`).
- Times people read are stored as KST ISO text (`timebase.py`, the only wall-clock reads).
- Docs: `README*.md` are Korean (callers and operators read them); `SPEC.md`, this file and every
  `<area>/__spec__.md` are English. Korean UI strings and what a person said are quoted verbatim.
  When behaviour changes, update the affected documents in the same change — stale docs are bugs.

## Verifying

```bash
SCANLABEL_TEST_DSN=postgresql://postgres@127.0.0.1:5433/scanlabel_test python -m pytest
```

## Branch

Work on `feature/<topic>` branches; never commit to `main` directly.
