# processing/ — the pure area

**What the numbers are.** Everything that turns a scan into a feature, two features into a score, scores into a
suggestion, a cluster or a map position — and the label tree's own rules. Functions in, plain dicts out.

Dependency rule: `processing` imports the standard library and itself, nothing else — no DB, no HTTP, no
clock, no sibling area (enforced by `tests/test_architecture.py::test_processing_stays_pure`, an allowlist).
That is what lets `jobs eval` judge a heuristic without a browser, a fleet or a connection.

Litmus test: a `psycopg` / `httpx` / `datetime.now` / `scanlabel.data` in these files is a violation. If a rule
needs a stored fact, the caller passes it in (`Tree`, `candidates`, `member_labels`).

## Per-file responsibilities

### `shape.py` — shape features, similarity, ranking, clusters, map, evaluation
- **Every threshold and weight is a module constant here and nowhere else** (`SL-SUG-4` · `SL-CLU-7`):
  `FEATURE_VERSION` · `PATH_N` · `GRID_N` · `MIN_OVERLAP` · `PROFILE_MIN` · `FLAT_REL` · `W_1D`/`W_2D` · `SUGGEST_MIN` · `MARGIN`
  · `EVIDENCE_MIN` · `LEVEL_TOP` · `CASE_TOP` · `CLUSTER_MIN` · `CLUSTER_MIN_SIZE` · `CLUSTER_MAX_N` ·
  `CLUSTER_AGREE` · `MAP_LANDMARKS`. A change to one is a behaviour change: run `jobs eval` on the real labels
  and put the numbers in the commit message.
- Features: `num` (poc_viewer's `num()` rule) · `channel_features` · `record_features` (the snapshot, before
  normalisation). Changing the snapshot's shape bumps `FEATURE_VERSION`.
- Comparison: `zscore` · `profiles` (X/Z profiles from the cells) · `prepare` (picks each channel's materials —
  area scan: X profile, Z profile, cells; line scan: the scan-order curve — and z-scores each once, on its own;
  scores exactly like the raw snapshot) · `similarity` (score + scale ratio, `None` when no common channel; an
  area scan and a line scan are not compared) · `normalized_profiles` (what the screen draws, `SL-SIM-1`).
- Reading scores: `rank` (per-level ranking with the tree roll-up, the "sure" levels) · `similar_cases` ·
  `cluster` (representative-seeded grouping) · `cluster_category` · `cluster_map` / `project` /
  `project_features` (landmark MDS, `SL-MAP-1`) · `evaluate` (leave-one-out).

### `tree.py` — the label tree's rules
- `Tree` (an immutable view over `label_defs` rows: path, ancestors, effective retirement, children) ·
  `check_selection` (no ancestor+descendant, no retired/missing id) · `child_level` (the level of a new node) ·
  `check_move` (same-level parent only). `LEVEL_NAMES` · `MAX_LEVEL = 3` · `PATH_SEP`.
- `data/repository.py` applies these to rows; the DB `CHECK`s are the second line, this is the first
  (and gives the 422 message).

## Decisions

### The area is pure, and numpy stays out
- **Decision:** `processing/` is stdlib-only; `math.sumprod` carries the n² comparison (800 records ≈ 6 s).
- **Reason:** nothing here needs an extension a closed-network host may not carry; purity is also what makes
  `jobs eval` and the tests DB-free.
- **Rejected:** numpy from the start (`SL-X-6`: not needed at this size; `shape.py` is the only file that would
  change once it is known to be fetchable); a classifier first (`SL-X-3`); pgvector (`SL-X-5`).
- **Revisit when:** thousands of records per scan kind make the pairwise pass the bottleneck (`SL-X-8`).
- **Validation:** `test_processing_stays_pure` · `test_prepared_features_score_exactly_like_raw_snapshots`.

## Test map
`tests/test_shape.py` — features, similarity, ranking, clusters, map, evaluation (the `SL-FEAT` / `SL-SIM` /
`SL-SUG` / `SL-CLU` / `SL-MAP` / `SL-EVAL` lines of SPEC.md). `tests/test_tree.py` — the tree rules (`SL-TREE`).
`tests/test_architecture.py` — the purity allowlist.
