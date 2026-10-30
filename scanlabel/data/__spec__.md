# data/ — persistence and the schema

**What is stored, and how the schema moves.** `repository.py` is the one PostgreSQL `Store` (raw SQL over a
psycopg pool, no ORM); `migrate.py` + `migrations/` are the schema's canonical form.

Dependency rule: `data` imports `processing` (the tree's rules) and `timebase`, nothing else of this package —
never `connectors` (a stored row does not know where it came from) and never `application`
(enforced by `tests/test_architecture.py::test_data_reaches_only_processing_and_timebase`).

## Table map

| Table | Holds | Written by | Goes away |
|---|---|---|---|
| `label_defs` | the three-level label tree (`SL-TREE`) | `/label-defs`, `jobs seed-labels`, migration `0003` | never deleted — `retired` |
| `scan_labels` | one labelled record: label ids + feature snapshot + meta copy (`SL-LBL`) | `PUT /labels`, cluster apply | empty selection deletes the row |
| `scan_records` | the record pool — every record seen, labelled or not (`SL-POOL`) | opening a file, labelling, `jobs index`, `jobs refeature` | not deleted |
| `index_cursors` | where `jobs index` stopped in fleet's `/generations` | `jobs index` | not deleted |
| `cluster_runs` · `cluster_members` · `cluster_points` · `cluster_maps` | the latest cluster run per set and measure (`method`: `skew` · `pca`, `SL-CLU-10`), its members, 2D map positions and one map projection per group (`SL-CLU` · `SL-MAP`) | `POST /clusters/run`, `jobs cluster` | replaced by the next run of the same measure |
| `schema_migrations` | the migration ledger | `migrate.py` | never |

A case link (`label_defs.case_id`) is a **soft** reference to fleet's `acs_case` — scanlabel never joins
across databases (`SL-CASE-3`).

## Per-file responsibilities

### `repository.py` — `Store`
- Verifies the schema version at start and **refuses** on a mismatch (`SchemaError`); it never applies.
- `ValueError` → 422, `KeyError` → 404 at the API; the tree rules come from `processing/tree.py`, the `CHECK`
  constraints are the second line.
- Times people read are KST ISO text (`timebase.py`); a pool row is rewritten only when its generation, feature
  version, scan kind, chuck or sensor changes, and that rewrite takes a new `seq` (`SL-POOL-3`). `chuck_id` ·
  `sensor_id` are columns of the pool, the members and the map points (migrations `0005` · `0006`) — the cluster group.
- A run's map projections are rows of `cluster_maps` (`run_id`, `group_key`), not one jsonb on the run (migration
  `0007` moved them): a PCA model is thousands of numbers per group, so `run_map` reads only the group asked for.

### `migrate.py` + `migrations/` — numbered forward-only SQL
- Order · ledger · checksum · advisory lock, and nothing else. A file is one transaction. **An applied file is
  immutable** (the checksum is of the file's bytes): a change is a new `NNNN_*.sql`, never an edit.
  `.gitattributes` keeps `.sql` free of line-ending conversion for the same reason.
- The runner finds `migrations/` by its own location, so the two move together. Moving them between folders
  changes no checksum and needs no migration (the 2026-10-19 area split did exactly that).
- `python -m scanlabel.data.migrate {up,status}` — `status` exits 1 when something is pending.

## Decisions

### Same rules as fleet's migrations, no shared code
- **Decision:** `migrate.py` follows `fleet/data/migrate.py` rule for rule but is its own file; the lock key
  differs (`0x7363_616E_6C01`) so both can live in one PostgreSQL instance with different databases.
- **Rejected:** importing fleet's runner (`SL-FLEET-1` — neither project imports the other).
- **Validation:** none beyond the runner itself (a checksum mismatch or a ledger version missing from the code
  raises `MigrationError`; `Store` raises `SchemaError` on a version mismatch); `tests/conftest.py` runs
  `migrate.up` once per session, so every migration applies to an empty database in CI.

## Test map
`tests/conftest.py` migrates a real PostgreSQL (`SCANLABEL_TEST_DSN`, never a silent skip) and truncates between
tests. `tests/test_api.py` (labels, tree, seed), `tests/test_cluster.py` (pool, runs, map),
`tests/test_taxonomy.py` (migration `0003` re-run for idempotence).
