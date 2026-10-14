# scanlabel — requirements and decisions

The canonical rules of this project. One line = one requirement, addressed by ID. A line carrying
`[test: …]` is guarded by that test (`tests/`); a line without one is guarded by nothing.
Caller contract: `README.api.md`. Working rules: `CLAUDE.md`.

## What this project is (SL-ROLE)

- `SL-ROLE-1` scanlabel lets a person put a **label** on one dump record (one raw scan, all its channels)
  and, for a record on screen, finds **labelled records whose shape is similar** and suggests their labels.
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
  constraints and `tree.py`. [test: "test_the_parent_sets_the_level_and_nothing_goes_below_a_leaf" · "test_tree_rules_are_enforced"]
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
- `SL-TREE-10` `labels.seed.example.json` is a **development placeholder**, not the real taxonomy.

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

- `SL-SIM-1` Each side is z-scored (offset and gain removed) and compared by Pearson correlation — 1D over
  the 128 points, 2D over the cells both sides have (at least `MIN_OVERLAP` = 16). Channel score =
  `W_1D`·1D + `W_2D`·2D over what is available; record score = mean over common channels.
  [test: "test_same_shape_scores_near_one_across_density_gain_and_offset" · "test_different_shapes_score_low"]
- `SL-SIM-2` **1D compares only records that walked the same trajectory.** A raster with a different row
  count is a different index order, so its 1D score is meaningless while its 2D score still holds. That is
  why the default scope is **the same `scan_enum`**. [test: "test_1d_needs_the_same_trajectory_but_2d_does_not"]
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
- `SL-SUG-4` Thresholds and weights are **heuristics** in `shape.py` constants, and only there. They are
  tuned by `SL-EVAL`, not by feel.
- `SL-SUG-5` Without a `source`, `/labels/similar` answers one suggestion per record of the file — what the
  record list marks.

## Measurement (SL-EVAL)

- `SL-EVAL-1` `jobs eval` runs leave-one-out over the labelled records: per level, top-1 and top-3 hit
  rates, how often a level was suggested and how often that suggestion was right. A record whose label
  stops above a level is not counted at that level. [test: "test_leave_one_out_counts_a_level_only_for_labels_that_reach_it" · "test_eval_reports_per_level_hit_rates"]
- `SL-EVAL-2` "Similar shapes share a cause" is a **hypothesis** until `jobs eval` says otherwise on real
  labels; real dump samples were not available when this was built (ftpmodule ROADMAP item 1).

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
- `SL-X-6` **numpy at the start** — not needed at this size; `shape.py` is the only file that would change
  once it is known to be fetchable.
