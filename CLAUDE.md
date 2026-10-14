# CLAUDE.md — scanlabel working rules

A FastAPI + PostgreSQL service that labels dump records with a three-level tree and suggests labels by
shape similarity. It reads fleet (ftpmodule) through fleet's public HTTP API only. **`SPEC.md` holds every
requirement and decision** — read it before changing behaviour, and edit it in the same change.

## Area map

| File | Responsibility |
|---|---|
| `scanlabel/shape.py` | **Pure** — snapshot features, similarity, ranking with the tree roll-up, leave-one-out evaluation. Every threshold and weight lives here, as a constant. Knows no DB, no HTTP |
| `scanlabel/tree.py` | **Pure** — the label tree's rules: path, ancestors, effective retirement, selection check, level of a new node, move check |
| `scanlabel/fleet_client.py` | The only place that calls fleet — `GET /servers`, `POST /servers/{id}/items/{set}` with `source: "cache"`; structural pairing of raw scans and metas; the short per-file cache |
| `scanlabel/store.py` | PostgreSQL — raw SQL over a psycopg pool. `ValueError` → 422, `KeyError` → 404. Verifies the schema version at start and refuses on a mismatch |
| `scanlabel/api.py` | FastAPI routes — contract in `README.api.md` |
| `scanlabel/jobs.py` | CLI — `seed-labels` · `eval` · `refeature` |
| `scanlabel/migrate.py` + `migrations/` | Numbered forward-only SQL, immutable once applied (checksum), one transaction per file |

## Rules

- **Never import fleet, never read its database.** A need fleet's API does not serve is a request to fleet,
  not a shortcut (`SL-FLEET-1`).
- **Never trigger FTP** — every item run is `source: "cache"` (`SL-FLEET-2`).
- Judgements stay here; poc_viewer transcribes them (`SL-ROLE-3`). Do not add a threshold to the front end.
- Changing a constant in `shape.py` is a behaviour change: run `jobs eval` on the real labels and write the
  numbers in the commit message.
- Changing the snapshot's shape bumps `FEATURE_VERSION`; run `jobs refeature` after deploying.
- Schema: a new `migrations/NNNN_*.sql`, never an edit to an applied one.
- Times people read are stored as KST ISO text (`timebase.py`, the only wall-clock reads).
- Docs: `README*.md` are Korean (callers and operators read them); `SPEC.md` and this file are English.
  Korean UI strings and what a person said are quoted verbatim.

## Verifying

```bash
SCANLABEL_TEST_DSN=postgresql://postgres@127.0.0.1:5433/scanlabel_test python -m pytest
```

## Branch

Work on `feature/<topic>` branches; never commit to `main` directly.
