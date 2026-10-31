# scanlabel — requirements and decisions

The canonical rules of this project. One line = one requirement, addressed by ID. A line carrying
`[test: …]` is guarded by that test (`tests/`); a line without one is guarded by nothing.
Caller contract: `README.api.md`. Working rules: `CLAUDE.md`.

## What this project is (SL-ROLE)

- `SL-ROLE-1` scanlabel lets a person put a **label** on one dump record (one raw scan, all its channels)
  and, for a record on screen, finds **labelled records whose shape is similar** and suggests their labels.
- `SL-ROLE-4` It also **groups every record it has seen — labelled or not — by shape** (clusters), reads each
  group's category off its labelled members, labels a group at once, and gathers the **cases** (fleet `acs_case`)
  that similar records point at (2026-10-15, the user's ask: 「그래프에 대한 클러스터링 포함 유사사례 카테고리화,
  유사사례 조회」).
- `SL-ROLE-2` It is a **separate project** from fleet (ftpmodule). fleet stays "fetch · parse · serve";
  shape analysis, labels and their growth (clustering, classifiers, numpy) live here.
  The user's words: 「ftpmodule 은 데이터를 가져와서 parsing 하는거 위주였는데 … 별도의 project 를 분기해서」.
- `SL-ROLE-3` poc_viewer only **enters and shows** labels. Every judgement (scores, whether something is
  suggested) is made here and transcribed there.

## Boundary with fleet (SL-FLEET)

- `SL-FLEET-1` scanlabel reads fleet **only through fleet's public HTTP API** (`README.api.md` of ftpmodule).
  It never reads fleet's database and never imports fleet code; fleet knows nothing about scanlabel.
- `SL-FLEET-2` Every item run is `source: "cache"` — fleet answers from generations it already parsed and
  **never opens an FTP session for us**. [test: "test_label_round_trip_and_rename_follows"]
- `SL-FLEET-3` A file fleet has not parsed yet is a 422 that tells the person to open it first; fleet
  unreachable is a 502; an unknown server is a 404. [test: "test_label_validation" · "test_fleet_down_is_502"]
- `SL-FLEET-4` Records are told apart **by structure** (`xyz`+`value` = raw scan, `results` = meta), never by
  `kind` — the same rule as fleet's `dump/interface.md`. A meta belongs to the raw whose `source` its `pair` names.
- `SL-FLEET-5` A machine is identified by fleet's `servername` (the equipment identity), not by the surrogate
  `server_id`; `server_id` is kept beside it for linking back.
- `SL-FLEET-6` One file's records are cached in-process for 120 s (8 files) so switching records does not
  re-download the file. Saving a label always reads fresh, so the stored `sig` is the one on screen.

## Label tree (SL-TREE)

- `SL-TREE-1` Labels are a **three-level tree** — 대분류 › 중분류 › 소분류 (`label_defs`, `level` 1–3).
  A 대분류 has no parent, a 중분류's parent is a 대분류, a 소분류's parent is a 중분류. Enforced by `CHECK`
  constraints and `processing/tree.py`. [test: "test_the_parent_sets_the_level_and_nothing_goes_below_a_leaf" · "test_tree_rules_are_enforced"]
- `SL-TREE-2` The tree is **chosen from, not typed**: free-text tags split one shape into spellings
  (`edge drop` / `엣지드롭`) and the suggestions split with them. When nothing fits, a new node is added
  **at the level where the person is** (`origin = 'added'`) and is an ordinary label from then on.
  The user's words: 「라벨은 어느정도 정해진게 있는거같은데 그걸 먼저 사용하되 없으면 라벨을 추가해서」.
- `SL-TREE-3` A name is unique **among siblings**, ignoring case and surrounding spaces — `피크` may sit under
  two different 대분류. [test: "test_tree_rules_are_enforced"]
- `SL-TREE-4` A non-empty `code` (the in-house classification code) is unique across the tree.
- `SL-TREE-5` A node moves only to a parent **of the same level**; its children follow, and records keep
  pointing at it by id. [test: "test_a_node_moves_only_to_a_parent_of_the_same_level"]
- `SL-TREE-6` A node is never deleted — it is `retired`. A retired node **and its descendants** leave the
  picking list; records already labelled keep it. [test: "test_retiring_hides_the_node_and_its_children" · "test_a_retired_ancestor_retires_its_descendants"]
- `SL-TREE-7` Renaming a node renames it on every record (records hold ids). [test: "test_label_round_trip_and_rename_follows"]
- `SL-TREE-8` The first tree arrives as a seed file (nested JSON or a `대분류,중분류,소분류[,코드,설명,사례번호]`
  CSV) through `jobs seed-labels`, which is **idempotent** — an existing sibling of the same name is reused,
  and only its empty code/description/case are filled. A CSV row's code and case attach to its deepest node.
  [test: "test_seed_is_idempotent_and_builds_three_levels" · "test_csv_seed_attaches_code_to_the_deepest_node"]
- `SL-TREE-9` A case link (`case_id` → fleet `acs_case`) is written **once, on the label node** — 「this shape
  is this case」. Shown for a node is its own case, else its nearest ancestor's.
  [test: "test_a_label_stopped_at_the_middle_level_carries_its_own_case"]
- `SL-TREE-10` `labels.seed.example.json` is a **development placeholder**, not the real taxonomy (that is
  `SL-TREE-12`).
- `SL-TREE-11` **Three levels, fixed — a deeper source is folded, not extended** (2026-10-15). The source table
  `0603.md` (`error_no › category › error`) has names that read like a fourth level — `WS › IFM-MIOB` ·
  `IFM-NC5` …, `C&T › Air shower-air balance` · `Air shower-leakage or damage`. Such a prefix group stays **inside
  the 소분류 name**; search still finds every `IFM-*` by `IFM` (`DUMP-LBL-4` matches names). Reason: suggestions
  and cluster agreement (`SL-SUG-2` · `SL-CLU-4`) need several records on one node, and a deeper level thins them
  further; per-level evaluation (`SL-EVAL-1`) assumes levels mean the same thing across branches.
  Rejected: a fourth level only where the source hints at one (ragged depth — level-wise rates and agreement no
  longer compare branch to branch); the prefix as the 소분류 and the part dropped (loses the part).
  Revisit when `jobs eval` shows the 소분류 splitting among one prefix's members while the prefix itself is sure —
  i.e. people know 「IFM」 but not which part, and stopping at the 중분류 throws that away.
  [test: "test_taxonomy_0603_is_a_fixed_three_level_tree_and_idempotent"]
- `SL-TREE-12` The real taxonomy **ID-0603** arrives with migration `0003` (대분류 = `error_no`, 중분류 =
  `category`, 소분류 = `error`; a blank `error` ends the branch at the 중분류 — `SW/Firmware`), in the file's order,
  `origin = 'seed'`, no codes (the source has none). It skips a node whose sibling name already exists, so a DB
  that loaded the same table through `jobs seed-labels` is safe. It does not touch the placeholder tree.
  [test: "test_taxonomy_0603_is_a_fixed_three_level_tree_and_idempotent"]

## Record labels (SL-LBL)

- `SL-LBL-1` One row per record — `(servername, set_name, name, source)` — holding one or more label ids.
- `SL-LBL-2` A label may stop at **any level**: a person who cannot tell the 소분류 labels the 중분류.
- `SL-LBL-3` An ancestor and its descendant are never picked together (the descendant implies it); a
  missing or retired id is rejected. [test: "test_an_ancestor_and_its_descendant_cannot_be_picked_together" · "test_label_validation"]
- `SL-LBL-4` Saving an empty selection deletes the row.
- `SL-LBL-5` A label carries **no prose**. Cause, action and remarks already live in fleet's per-file analysis
  and in the cases; a label only classifies.
- `SL-LBL-6` The row keeps a **feature snapshot** and a display copy of the meta (scan kind, chuck, sensor,
  times). fleet's cache is pruned (`jobs prune`), and a label must outlive it.

## Shape features (SL-FEAT)

- `SL-FEAT-1` Per channel the snapshot holds, **before normalisation**: the scan-order array reduced to
  `PATH_N` = 128 points (bin means; a shorter array is linearly interpolated), the (x, z) plane min–max
  scaled into `GRID_N`×`GRID_N` = 16×16 cell means (empty cell = null), and `n · min · max · mean · std`.
  Changing only how values are normalised therefore needs no source data.
  [test: "test_snapshot_keeps_values_before_normalisation" · "test_a_short_scan_is_interpolated_to_full_length"]
- `SL-FEAT-2` Values are read like poc_viewer's `num()`: a numeric string is a number; `""`/`null`/NaN are
  dropped, never read as 0. [test: "test_blank_and_none_are_dropped_not_read_as_zero"]
- `SL-FEAT-3` Channels are matched **by their `value` key only**. The detector → channel rule
  (poc_viewer `DUMP-CH-2…6`) is not copied here.
- `SL-FEAT-4` A scan whose points lie on a line or a point has no 2D feature.
  [test: "test_a_line_scan_has_no_2d_and_compares_by_1d_only"]
- `SL-FEAT-5` `FEATURE_VERSION` changes when the snapshot's shape changes; `jobs refeature` rebuilds the
  stale ones from fleet's cache and reports those whose generation is gone.

## Similarity (SL-SIM)

- `SL-SIM-1` What a channel is compared by depends on what it is, and **every material is z-scored on its own**
  (offset and gain removed) before a Pearson correlation. An **area scan** (it has the 16×16 cells): 1D = the
  mean of the **X profile** (column means of the cells) and **Z profile** (row means) correlations, each over the
  slots both sides have (at least `PROFILE_MIN` = 8); 2D = the cells both sides have (at least `MIN_OVERLAP` = 16).
  A **line scan** (no cells, `SL-FEAT-4`): 1D = the 128-point scan-order curve. An area scan and a line scan are
  not compared. Channel score = `W_1D`·1D + `W_2D`·2D over what is available; record score = mean over common
  channels. Profiles are derived from the stored cells, so this needs no source data and no `FEATURE_VERSION`
  bump (`SL-FEAT-1`). The screen draws the same materials (`profiles`, README.api.md).
  [test: "test_same_shape_scores_near_one_across_density_gain_and_offset" · "test_different_shapes_score_low" · "test_x_profile_z_profile_and_grid_are_normalised_each_on_its_own" · "test_a_line_scan_and_an_area_scan_are_not_compared"]
  Rejected (2026-10-28): the scan-order curve for area scans too. Every pooled record (7,248 on 2026-10-28,
  COARSE/FINE/MEDIUMHORVERT_SCAN) is an area scan; bin-averaging 320–2,400 points in measurement order into 128
  mixed the row turns into the bins, so the 1D half of the score — and the curve the screen drew — carried
  no shape (the user: 「1d / 2d 를 한꺼번에 normalize 하면 안될거같은데 그렇게 하니깐 그래프가 뭉개지는거같아서」 —
  normalising 1D and 2D together seems wrong; it smears the graph). Validation: `jobs eval` (leave-one-out,
  535 labels, `same_scan_only`) top-1/top-3 = 0.998 at all three levels both before and after; the run went
  134 s → 92 s.
- `SL-SIM-2` **A line scan's 1D compares only records that walked the same trajectory** — its curve is in index
  order. An area scan's profiles do not depend on the order (a raster with a different row count matches).
  The default scope stays **the same `scan_enum`**: another kind is another measurement.
  [test: "test_a_2d_scan_compares_by_x_and_z_profiles_not_by_scan_order"]
- `SL-SIM-3` A flat channel (range ≤ `FLAT_REL` of its magnitude) is not correlated: flat vs flat = 1,
  flat vs anything else = 0 — normalising a flat signal turns noise into a shape.
  [test: "test_flat_matches_flat_and_nothing_else"]
- `SL-SIM-4` Normalisation throws magnitude away, so the **scale ratio** (std of the other / std of this,
  geometric mean over channels) is returned beside the score and never mixed into it.
  [test: "test_scale_ratio_is_reported_not_mixed_into_the_score"]
- `SL-SIM-5` No common channel = no comparison. [test: "test_no_common_channel_means_no_comparison"]
- `SL-SIM-6` A record is never compared with itself.
  [test: "test_similar_excludes_itself_and_respects_scan_kind"]

## Suggestion (SL-SUG)

- `SL-SUG-1` A labelled record votes for **its node and every ancestor** — a 소분류 label is also a vote for
  its 중분류 and 대분류. Node score = the best score of the records under it; evidence = how many of them
  score ≥ `EVIDENCE_MIN`. [test: "test_a_leaf_label_also_votes_for_its_ancestors"]
- `SL-SUG-2` A level is **sure** only when its top node scores ≥ `SUGGEST_MIN`, leads the runner-up by
  ≥ `MARGIN`, and (below the top) is a child of the sure node one level up. The suggestion is the deepest
  sure level — 「대분류는 확실, 소분류는 갈림」 is said as such.
  [test: "test_a_split_leaf_stops_the_suggestion_at_the_middle_level" · "test_nothing_is_suggested_below_an_unsure_level"]
- `SL-SUG-3` Below the threshold nothing is suggested. A forced suggestion would read as evidence.
- `SL-SUG-4` Thresholds and weights are **heuristics** in `processing/shape.py` constants, and only there. They are
  tuned by `SL-EVAL`, not by feel.
- `SL-SUG-5` Without a `source`, `/labels/similar` answers one suggestion per record of the file — what the
  record list marks.

## Similar cases (SL-CASE)

- `SL-CASE-1` `/labels/similar` (with a `source`) answers `cases`: the cases the **similar labelled records** point
  at, one entry per case — each record votes, for every label it carries, for that label's case (`tree.case_for`,
  `SL-TREE-9`). Case score = the best score among its records, `count` = how many; only records scoring
  ≥ `EVIDENCE_MIN` vote (an unlike record's case is not evidence); at most `CASE_TOP` = 5.
  [test: "test_similar_cases_are_grouped_by_case_and_ignore_unlike_records" · "test_similar_cases_and_records_of_a_case"]
- `SL-CASE-2` `GET /labels/by-case/{case_id}` goes the other way — the labelled records whose label resolves to that
  case (its own or an ancestor's `case_id`), newest first. [test: "test_similar_cases_and_records_of_a_case"]
- `SL-CASE-3` A case's title and body stay in fleet (`/cases/{id}`); scanlabel carries only the id.

## Record pool (SL-POOL)

- `SL-POOL-1` `scan_records` holds one feature snapshot per record scanlabel has **seen**, labelled or not — the
  material clustering needs, since `scan_labels` holds only labelled records. A record enters when its file's
  suggestions are asked for (= the file was opened on screen), when it is labelled, or through `jobs index`.
  Migration `0002` copies the already-labelled records in. [test: "test_opening_a_file_and_labelling_put_records_in_the_pool"]
- `SL-POOL-2` `jobs index` follows fleet's `GET /generations` with its own cursor (`index_cursors`), reads each new
  file **from cache only** and puts its records in the pool; a file whose cache is gone is reported as `skipped`.
  It is the periodic way to include files nobody opened. [test: "test_index_follows_the_generation_cursor_without_ftp"]
- `SL-POOL-3` A pool row is rewritten only when its generation, feature version, scan kind, chuck or sensor changes; that rewrite
  takes a new `seq`. Re-opening an unchanged file changes nothing — so it does not make the clusters stale.
- `SL-POOL-4` `jobs refeature` rebuilds stale pool rows as well as labelled ones: one comparison definition across
  the pool.

## Clusters (SL-CLU)

- `SL-CLU-1` A cluster run groups the pool and is **stored** (`cluster_runs` + `cluster_members`, only the latest
  run per set **and measure** (`SL-CLU-10`) is kept — a re-run carries the last one forward, `SL-CLU-13`), so the screen, the cluster numbers and a bulk label all refer to the same result. It runs on
  request (`POST /clusters/run`, the screen's 「다시 묶기」) or from `jobs cluster`; nothing re-clusters behind a
  reader's back. A run remembers the pool's highest `seq`; a higher one in the pool makes it `stale`.
- `SL-CLU-2` The method is **representative-seeded grouping**: all pairs within one group (`SL-CLU-3`) are compared
  by the **inter-channel skew score** (`SL-CLU-9`), the record with the most neighbours ≥ `CLUSTER_MIN` (0.80) becomes a
  representative, it and its remaining neighbours
  form a cluster, repeat; groups smaller than `CLUSTER_MIN_SIZE` (3) stay unclustered. Every member resembles the
  representative **directly**, so a cluster cannot chain unlike shapes together; ties break by neighbour score sum,
  then id — deterministic.
  [test: "test_clusters_group_like_shapes_and_leave_the_rest_unclustered" · "test_clusters_never_mix_scan_kinds"]
- `SL-CLU-3` Clusters never cross a **group = (scan kind, chuck, sensor)** (`SL-SIM-2`; migrations `0005` · `0006`).
  Different scan kinds walked different trajectories; different sensors or chucks on one kind read on a different
  baseline, so mixing them makes "a different sensor/chuck" look like "a different shape" (2026-10-28, the user:
  「실제 clustering 할때 데이터에서 sensor_id + scan_enum 까지 grouping 해야할거같은데」; 2026-10-30: 「scan_enum / chuck_id /
  sensor_id 가 grouping key 로 묶어야하고」). The chuck and sensor are `meta.chuck_id` · `meta.sensor_id` as text, `""`
  when absent, stored as `scan_records.chuck_id` · `scan_records.sensor_id`. Each group is capped at the `CLUSTER_MAX_N` (800) most
  recently changed **unlabelled** records — labelled ones are always in (`SL-CLU-12`) — the comparison is n², measured at ≈ 6 s for 800 (`math.sumprod`, prepared z-scores,
  `shape.prepare`, which scores exactly like the raw snapshot); the cut is reported as `truncated`.
  [test: "test_prepared_features_score_exactly_like_raw_snapshots" · "test_clusters_never_mix_sensors_of_one_scan_kind" · "test_clusters_never_mix_chucks_of_one_scan_kind_and_sensor" · "test_clusters_and_the_map_are_per_scan_kind_and_sensor" · "test_clusters_and_the_map_are_per_scan_kind_chuck_and_sensor"]
  Not yet: the suggestion scope (`same_scan_only`, `SL-SIM-2`) is still the scan kind alone.
- `SL-CLU-4` A cluster's **category** is read off its labelled members per level, each label counting for its
  ancestors (`SL-SUG-1`): a level agrees when its top node holds ≥ `CLUSTER_AGREE` (0.60) of the labelled members,
  more than the runner-up, and is a child of the agreed node one level up. The category is the deepest agreed
  level, or none. [test: "test_cluster_category_is_the_deepest_level_the_labelled_members_agree_on"]
- `SL-CLU-5` `POST /clusters/{no}/apply` labels a cluster's members at once (by default only the unlabelled ones,
  optionally a chosen subset) from the pool's snapshots — fleet is not called. The selection is checked once before
  anything is written; a `run_id` other than the latest is a 422, so a re-run never redirects a bulk label.
  [test: "test_a_cluster_label_is_applied_to_its_unlabelled_members_at_once"]
- `SL-CLU-6` `/labels/similar` (with a `source`) answers `cluster`: the cluster the record belongs to in the latest
  run, with its category — the screen says 「이 기록은 묶음 #n 에 든다」 without a second call.
- `SL-CLU-7` Thresholds (`CLUSTER_MIN`, `CLUSTER_MIN_SIZE`, `CLUSTER_AGREE`, `CLUSTER_MAX_N`) are heuristics in
  `processing/shape.py`, like `SL-SUG-4`, and the run stores the values it used.
- `SL-CLU-8` `POST /clusters/run` **starts** the run in a background thread and answers 202 with the job; `GET
  /clusters` carries `job` (`running` · `done` · `failed` + reason). A second request while one runs gets the running
  job, not a second run. The job state lives in the API process's memory (one process; `jobs cluster` from the CLI
  is not seen there — its result is, through the stored run). Reason (2026-10-15): the run took 18 s on 2,400 records
  and the front end's proxy hung up first, so the screen saw a 500. [test: "test_rerun_returns_at_once_and_reports_its_state"]
  Rejected: a longer proxy timeout (the run grows n², and any request holding a proxy socket for tens of seconds
  fails somewhere else next). `GET /clusters` reads the job **before** the stored run — the other order could answer
  `done` with the run from before it (or none) when the job ends between the two reads (seen as a flaky test,
  2026-10-30).
- `SL-CLU-9` **Clusters and the map are measured by inter-channel skew, not by shape** (2026-10-30, the user:
  「value 하위에 있는 key 의 array 간의 skew (tr, txh, tyh 등 같은 index 값들간의 그래프의 편차)가 dump error 의 labeling
  clustering 의 key」 — the skew between the `value` arrays, the spread of tr · txh · tyh at the same index, is what
  tells dump errors apart). Per record: every channel's materials (area scan: cells, X profile, Z profile and, since
  `SL-CLU-11`, the scan-order curve; line scan: the scan-order curve — the slots line up across channels because the
  channels share `xyz`) are scaled by **that
  channel's own point mean and std** from the snapshot (`stats`) — a cell mean is linear in the points, so this equals
  scaling each point and then averaging, and needs no source data and no `FEATURE_VERSION` bump. At every slot the
  **deviation** of each channel from the mean over channels is taken (a slot any channel lacks is skipped). The group's
  **baseline** — the per-slot **median** deviation over the group's records with the group's most common channel set
  (`skew_baseline`, none under `CLUSTER_MIN_SIZE` records) — is subtracted, leaving what is unusual for this record.
  Two records score `(2·u·v + f) / (|u|² + |v|² + f)` over the slots both have, `f = 2·n·SKEW_FLOOR²` (`SKEW_FLOOR` =
  0.10 channel-std RMS): 1 = same place, direction and size; two records whose channels agree (both under the floor)
  score near 1; one skewed and one not near 0; opposite near −1. Area: `W_1D`·mean(X, Z) + `W_2D`·cells + `W_SEQ`·curve
  (`SL-CLU-11`); line: the curve.
  Fewer than two common channels = no score (the record never joins a cluster; map distance 1). The run stores each
  group's baseline in its projection (`maps`), and the record detail scores and draws against it. Suggestions
  (`SL-SUG`, `rank`) still use shape similarity (`SL-SIM`) — the change is for clustering only.
  [test: "test_skew_compares_where_channels_part_not_the_shared_shape" · "test_the_baseline_takes_away_the_usual_offset_between_channels" · "test_skew_needs_two_common_channels_and_uses_only_the_common_ones" · "test_prepared_skew_scores_exactly_like_raw_snapshots" · "test_clusters_group_by_where_channels_part"]
  Why the baseline: when one channel always runs opposite (or ahead of) the others, that fixed deviation is the same in
  every record and buries the error's deviation — without it, two records skewed at opposite ends of the scan scored
  0.96 in the synthetic test. Rejected: Pearson correlation of the deviations (z-scoring them again throws away the
  size of the skew, and turns a record whose channels agree into noise that correlates at random); the mean as the
  baseline (one large cluster would pull it toward its own skew and erase it).
  Validation (local pool, 5,044 records, 714 labels, demo channels `A`/`B` — **not** real tr/txh/tyh data, and the
  labels were given by shape): label pairs in the same group scoring ≥ 0.8 — same label 59 % / different label 6 %
  (skew without the baseline: 98 % / 51 %; shape: 88 % / 1 %). Clusters: skew 89 clusters, 177 unclustered, label
  purity 0.869; shape with the same groups 121 / 114 / 0.984; before this change (shape, (scan kind, sensor)) 82 / 74 /
  0.984. `jobs cluster` 39 s (shape 42 s). Revisit when real tr/txh/tyh records are labelled: compare the two
  measures' purity there, and tune `SKEW_FLOOR` / `CLUSTER_MIN` for skew.
- `SL-CLU-10` **Two measures, both stored, the screen picks one**: `skew` (`SL-CLU-9`) and `pca` (principal
  components). One re-run (`POST /clusters/run`, `jobs cluster`) reads the pool once and stores a run per measure
  (`cluster_runs.method`, migration `0007`); every cluster / map / record route takes `method` (default `skew`, any
  other value is a 422), and a bulk label names the measure of the run it saw. `jobs cluster --method skew|pca` runs one.
  PCA, per group (`SL-CLU-3`), from the records with the group's most common (kind, channel set) — others get no
  coordinates, never cluster and have no point: each record becomes one row of three **deviations from the group's
  per-position median** — `shape` (each channel's own-std-scaled materials), `skew` (the per-slot inter-channel
  deviation of `SL-CLU-9`) and `amp` (each channel's log std) — weighted `PCA_W` (0.4 · 0.4 · 0.2; area materials
  `PCA_MAT_W` X 0.2 · Z 0.2 · cells 0.3 · scan-order curve 0.3 since `SL-CLU-11`, cells reduced to `PCA_GRID` 8×8, the
  curve to `PCA_PATH` 64) and scaled
  to RMS per value. Components are kept until `PCA_VAR` (0.90) of the variance is explained (at least two, at most
  `PCA_MAX_K` 10), by subspace iteration + Rayleigh–Ritz in the standard library — deterministic, sign fixed by the
  largest loading. Two records score `(2·u·v + f) / (|u|² + |v|² + f)` on their coordinates, `f = 2·PCA_FLOOR²`
  (0.10) — the skew score's form; clustering is `SL-CLU-2` with that score. The map is the first two coordinates
  as they are (a projection, not an approximation; the axes mean something), and the run stores the model so a record
  outside the run lands by the same linear projection. `GET /clusters/map?method=pca` adds `axes` (each axis's share
  of variance, its weight split by (part, channel), its loading curves), `GET /records/{id}?method=pca` adds `pca`
  (the record's distance from the group's usual and where it lies) and `vs.pca_similarity`.
  `jobs cluster` prints, per measure, the label purity per level and coverage (`shape.cluster_quality`) and the two
  partitions' adjusted Rand index (`shape.partition_agreement`) — the numbers to compare the two on real data.
  [test: "test_pca_groups_by_where_channels_part_and_places_records_on_the_axes" · "test_pca_scores_like_skew_and_needs_the_scopes_channel_set"]
  Note: amplitude is one of the materials, so a group whose records differ mostly in gain splits by gain under `pca`
  (it does not under `skew`). Not yet: the measures have not been compared on real tr/txh/tyh records — the API
  default stays `skew` until they are.

- `SL-CLU-11` **An area scan's skew also reads the scan-order curve** (2026-10-31, the user, asking why the data
  「경로겹쳐보기에서 사용되는 인덱스 - 값」 — the index → value series of the dashboard's path overlay — is neither
  clustered, put through PCA nor plotted in the record detail: 「그방법도 검토해서 반영해주고」). The 128-slot scan-order
  curve the snapshot already keeps (`SL-FEAT-1`; no `FEATURE_VERSION` bump) becomes a fourth skew material for area
  scans and a fourth PCA material (`_SKEW_MATS` · `_PCA_MATS`). Its axis is **scan progress** (slot j = the j-th 1/128 of
  the record's points), not the raw index: within one group the point count differs between records (local pool
  2026-10-31: 9–78 distinct counts per group, e.g. 420 vs 480 points), so raw index i is a different place in two
  records, while **within** a record every channel's index i is the same point (all 5,044 pooled records: one point
  count across their channels). Why it is not `SL-SIM-1`'s rejected curve: bin-averaging mixes row turns into a slot,
  which smears **one channel's shape**, but the same points fall into the slot for every channel, so the **deviation
  between channels** survives. What it adds: the cells and profiles fold every visit to a place into one slot — a
  HORVERT scan walks the area twice (horizontal raster, then vertical), so a skew in only one pass looked the same
  wherever it happened; the curve tells which pass, i.e. **when** in the scan the channels parted. Weight `W_SEQ` 0.5
  (cross-section · cells · curve one third each); the record detail returns the curve's skew as `line` beside `x` · `z`
  and its score as `dp`. Old stored baselines and PCA models (no curve) still read: a baseline lacking a material is not
  subtracted (`_base_fits`), a model projects with the materials it was built from (`_model_mats`); 「다시 묶기」 stores
  new ones.
  [test: "test_an_area_scans_skew_also_reads_the_scan_order_curve" · "test_a_stored_baseline_or_model_without_the_scan_order_curve_still_reads"]
  Rejected: comparing the raw index → value series across records (the point counts differ, see above); `W_SEQ` 0.25 —
  then a pair whose cells and profiles agree can never fall below `CLUSTER_MIN` on the curve alone, so two records
  that skewed in different passes still merge (the HORVERT test fails); shape similarity (`rank`) on the curve
  (`SL-SIM-1` stands — the curve stays out of label suggestions).
  Validation (local pool, 5,044 records, 714 labels, demo channels `A`/`B` — **not** real tr/txh/tyh data; measured
  in memory, nothing stored): skew — clusters 89 → 295, unclustered 177 → 197, purity L1/L2/L3 0.993/0.985/0.873 →
  1.0/0.999/0.913, coverage 0.964 → 0.955 (`W_SEQ` 0.25: 216 · 208 · L3 0.921 · 0.945); pca — clusters 57 → 100,
  unclustered 25 → 48, purity unchanged (0.985/0.985/1.0), coverage 0.999 → 0.994, model + clustering 46 s → 105 s
  (curve weight 0.15: 76 clusters); skew-vs-pca adjusted Rand 0.716 → 0.552. The finer split is the curve telling apart
  records whose skew sits in different parts of the scan; whether that is the right granularity can only be judged on
  real tr/txh/tyh labels. Revisit when they are labelled: tune `W_SEQ` and the PCA curve weight there.

- `SL-CLU-12` **A labelled record is always clustered and always on the map** (2026-10-31, the user: 「묶기 할때 한번이라도
  라벨링 된 데이터는 항상 별도 관리되어서 항상 묶음지도에 나와야할거같은데」 — a record labelled even once must always be
  in the clusters and on the map). `Store.pool` takes, per group, **every labelled record** plus the `CLUSTER_MAX_N` most
  recently changed unlabelled ones; `truncated` counts unlabelled records only. Before, the cap ranked by `seq` alone and
  labelling does not move `seq` (`SL-POOL-3`), so once 800 newer records arrived in a group its labelled records dropped
  out of the run, the map and the clusters' categories. Cost: the n² comparison grows with the labelled count; revisit
  when one group's labelled records near the cap. [test: "test_labelled_records_always_stay_in_the_pool_past_the_cap"]
- `SL-CLU-13` **A re-run carries the last run forward** (2026-10-31, the user: 「다른사람들이 각 엔지니어들이 동일한걸 검색하고
  다시묶기를 누르면 계속 다른 결과가 나오는거 아닌가, 한명만 쓰는 프로그램이 아닌데」 → 「안정적이 결과가 잘 변하지 않게
  사람들이 정합성에 문제가 없다고 느끼는 방향으로」 — many engineers press 다시 묶기; the result must stay put so people
  trust it). `shape.cluster(previous=…)` (`Store.carry_of` reads the latest run of the measure): per group, each last
  cluster re-seats its representative (if it left the pool: the remaining member with most neighbours among them);
  last members still ≥ `CLUSTER_MIN` to it **stay**; every other record (new, or unclustered last time) **joins** the
  carried cluster whose representative it resembles most (≥ `CLUSTER_MIN`); a carried cluster under `CLUSTER_MIN_SIZE`
  is dissolved; the rest is clustered as in `SL-CLU-2`. A carried cluster keeps its **number**; a new one takes the
  run's `next_no` onward — a number is never reused (migration `0008`: `cluster_runs.next_no`). The run stores
  `changes` (`shape.run_changes`: kept · moved · joined · released · out · new · closed), which `GET /clusters` returns
  and the screen states (poc_viewer `DUMP-CLU-19`). The list is ordered by size, not by number. `jobs cluster --fresh`
  ignores the last partition (numbers still continue) — for after a threshold or material change. Still one stored
  run per measure; a bulk label still names its `run_id` (`SL-CLU-5`). The map is recomputed per run, so points may
  shift; numbers and members are the reference.
  [test: "test_reclustering_keeps_numbers_and_lets_new_records_join" · "test_a_carried_cluster_survives_losing_its_representative_and_a_dissolved_number_is_not_reused" · "test_a_fresh_run_ignores_the_previous_partition_but_not_its_numbers" · "test_pressing_rerun_again_keeps_cluster_numbers_and_says_what_changed"]
  Validation (local pool, 5,044 records, skew; the newest 10% per group held back, clustered, then added and re-run):
  from scratch 795 of 4,337 previously clustered records kept their number (numbers were size ranks), partition
  ARI vs the last run 0.986; carried 4,319 / 4,337 kept (moved 9, released 9, joined 507, new clusters 21), ARI 0.998;
  pressed again with nothing new: all 4,835 kept, no change. 177 s for the four runs.
  Rejected: scheduled-only re-runs (a reviewer who labels a batch waits a day to see it; carrying makes a press cheap
  in consequence instead of rare); keeping every run as history (the screen would have to say which run a number
  belongs to — carried numbers make the latest run the one reference). Revisit when authorship exists (SSO): record
  who re-ran.

## Docs for readers (SL-DOC)

- `SL-DOC-1` **The service that judges also explains** (2026-10-31, the user: 「해당 데이터를 보고 리뷰하는사람들이 해당
  그래프 데이터를 보면 이해가 안되는데 어떤 방식 접근, 어떤 데이터들을 가져와서 해당 결론에 도출하는지 쉽고 이해할수
  있게 가이드 하는 문서도 해당 메뉴 링크에 있으면 좋을거같아」 — reviewers cannot follow the graphs; a guide to the
  approach, the data and how a conclusion is reached should hang off that menu). `GET /guide` serves
  `application/guide.html` — Korean, for reviewers: what is read from a dump record, the three summaries, both
  measures, the grouping, the map, and every block of the record detail with whether it feeds a score. Every threshold
  and weight in it is a `{{NAME}}` filled from `shape` at request time (`api.guide_values`), so the page cannot quote a
  stale number. Sections carry stable anchors (`#skew` · `#pca` · `#path` · `#detail` …) that poc_viewer links to
  (`DUMP-CLU-18`). [test: "test_the_guide_is_served_with_the_numbers_the_code_uses"]
  `docs/cluster-logic.html` stays the developer's walk through the code (request flow, tables), not the reader's guide.

## Cluster map (SL-MAP)

- `SL-MAP-1` A cluster run also places every record it covered — clustered or not — in 2D, **per group (scan kind,
  chuck, sensor)** (`cluster_points`, migration `0004`): near = alike **in inter-channel skew** (`SL-CLU-9`, the
  clusters' own measure). Method: landmark MDS — `MAP_LANDMARKS` (24) records picked max-min apart by distance
  `1 − skew score`, classical MDS on those, every other record projected from its distances to
  the landmarks only (24 comparisons each). It is an approximation and is said to be one.
  [test: "test_the_map_puts_like_shapes_together_and_unlike_ones_apart"]
- `SL-MAP-2` The run stores the projection per group (`cluster_maps`, one row per group since `0007` — a PCA model is
  thousands of numbers, so a read loads only its group's: landmark ids, pseudo-inverse, mean, skew baseline; for `pca`
  the model and the skew baseline), so a record
  **outside the run** — seen after it, or cut by the cap — is placed on the same map without re-running: its features
  (from the pool, else fleet's cache) against the landmarks. Such a target says `in_run: false` and has no cluster.
  [test: "test_a_record_outside_the_run_lands_where_its_twin_is" · "test_the_map_places_every_record_and_the_one_on_screen"]
- `SL-MAP-3` `GET /clusters/map` answers one group (`scan_enum` + `chuck_id` + `sensor_id`; default: the asked record's,
  else the largest; a run from before `0006` keyed its maps without the chuck and answers `has_map: false`): points
  `{id, x, y, c, l}` (cluster number or null, labelled or not), each cluster's size and agreed category, and the asked
  record's place. `GET /records/{id}` is a pool record's detail — labels, cluster, normalised curves and skew curves,
  and with `vs_*` its shape and skew scores against the record on screen. **Every drawn material is normalised from
  its own record only** (plus the group's baseline for skew), so the record on screen draws the same whichever point is
  opened; the comparison lives in the scores. A group under 3 records has no map.
  [test: "test_a_tiny_group_has_no_map"]
- `SL-MAP-4` Rejected: t-SNE/UMAP (no numpy, `SL-X-6`; and neither places a new record without re-running);
  full classical MDS (an n×n eigenproblem — 800² in pure Python); drawing clusters by colour (the screen's decision,
  poc_viewer `DUMP-CLU-8`).

## Measurement (SL-EVAL)

- `SL-EVAL-1` `jobs eval` runs leave-one-out over the labelled records: per level, top-1 and top-3 hit
  rates, how often a level was suggested and how often that suggestion was right. A record whose label
  stops above a level is not counted at that level. [test: "test_leave_one_out_counts_a_level_only_for_labels_that_reach_it" · "test_eval_reports_per_level_hit_rates"]
- `SL-EVAL-2` "Similar shapes share a cause" is a **hypothesis** until `jobs eval` says otherwise on real
  labels; real dump samples were not available when this was built (ftpmodule ROADMAP item 1).

## Structure (SL-ARCH)

- `SL-ARCH-1` The code is four areas, like fleet's: `processing/` (pure: `shape` · `tree`), `connectors/` (fleet over
  HTTP), `data/` (PostgreSQL `Store` + migrations), `application/` (`usecases` · `api` · `jobs`). Dependencies point
  down only — `processing` ← `data` ← `application`, `connectors` ← `application`; `processing` imports the
  standard library and itself. `timebase.py` is the one top-level module (the only wall-clock reads).
  [test: "test_processing_stays_pure" · "test_connectors_know_nothing_of_the_other_areas" · "test_data_reaches_only_processing_and_timebase" · "test_wall_clock_reads_live_only_in_timebase"]
- `SL-ARCH-2` `api.py` and `jobs.py` are adapters over `usecases.py` and never import each other (2026-10-19: `api.py`
  used to import `jobs.py` for `pool_rows` / `run_clusters`). [test: "test_the_thin_adapters_do_not_call_each_other"]
- `SL-ARCH-3` Each area's reference doc is its `__spec__.md`; a settled decision is recorded once in the owning one
  (Decision / Reason / Rejected / Revisit when / Validation). [test: "test_every_area_has_its_spec"]
- `SL-ARCH-4` The 2026-10-19 split moved files only: no table, column or migration changed, and the ledger's
  checksums (of file bytes) are untouched, so an existing database needs nothing. The entry points did move —
  `scanlabel.application.api:app` · `scanlabel.application.jobs` · `scanlabel.data.migrate`.
  [test: "test_migrations_sit_next_to_their_runner"]
  Rejected: re-export shims at the old module paths (they would keep the flat layout importable and hide
  stale callers; the only callers are the workspace's launch/task configs and the docs, updated with this change).

## Rejected (SL-X)

- `SL-X-1` **Doing this inside fleet** (an `application/shape.py` + `scan_labels` in fleet's schema). It would
  have been the first analysis computation and the first stored derived data in a module whose role is
  fetching and parsing. Rejected by the user in favour of a separate project (`SL-ROLE-2`).
- `SL-X-2` **Computing features in the browser** with the server storing opaque vectors — it keeps the feature
  definition in TypeScript, leaves no way to evaluate or suggest without a browser, and has to move the
  day labels number in the thousands.
- `SL-X-3` **A classifier (SVM/RF/CNN) first** — with tens of labels it only overfits; nearest-neighbour
  ranking works from the first label and shows its evidence. Revisit at hundreds of labels per class.
- `SL-X-4` **Free-text tags** — see `SL-TREE-2`.
- `SL-X-5` **pgvector** — tens to thousands of labels are compared exhaustively in Python in tens of ms;
  an extension is one more thing a closed-network PostgreSQL has to carry.
- `SL-X-6` **numpy at the start** — not needed at this size; `processing/shape.py` is the only file that would change
  once it is known to be fetchable. Clustering kept it out too: `math.sumprod` brought 800 records from ≈ 60 s to
  ≈ 6 s (2026-10-15).
- `SL-X-7` **Clustering on the fly, unstored** — cluster numbers would shift between the list, the detail and a
  bulk label as records arrive, and a bulk label must name exactly the members the person looked at (`SL-CLU-5`).
- `SL-X-8` **k-means / hierarchical (average-linkage) clustering** — k-means needs k and a vector space the
  correlation score is not; average linkage in pure Python is O(n³) at this size and its clusters have no member
  that every other member resembles, so a cluster cannot be shown by one representative curve. Revisit with numpy
  and thousands of records per scan kind.
- `SL-X-9` **Single-linkage (connected components of the ≥ 0.8 graph)** — chains: A~B~C joins A and C however
  unlike they are.
