"""모양 특징 · 유사도 · 단계별 제안 — 순수 함수 (SPEC.md 「모양 특징」·「유사도」·「제안」)."""
from __future__ import annotations

import pytest

from scanlabel.processing import shape
from scanlabel.processing.tree import Tree

from . import synth


def feats(fn, **kw):
    return shape.record_features(synth.scan(fn, **kw))


def test_same_shape_scores_near_one_across_density_gain_and_offset():
    # 같은 경로를 더 촘촘히 지난 스캔(열 3배) + 크기 · offset 차이
    a = feats(synth.peak(), cols=30, rows=20)
    b = feats(synth.peak(), cols=90, rows=20, gain=7.5, offset=-300.0)
    sim = shape.similarity(a, b)
    assert sim["score"] > 0.97
    assert sim["d1"] > 0.97 and sim["d2"] > 0.97


def test_1d_needs_the_same_trajectory_but_2d_does_not():
    # 행 수가 다른 raster 는 인덱스 순서가 다른 경로다 — 1D 는 비교가 안 되고 2D 만 맞는다.
    # 그래서 기본 비교 범위가 「같은 스캔 종류끼리」다 (SPEC.md 「한계」).
    sim = shape.similarity(feats(synth.peak(), cols=30, rows=20), feats(synth.peak(), cols=60, rows=40))
    assert sim["d2"] > 0.97
    assert abs(sim["d1"]) < 0.3


def test_different_shapes_score_low():
    a = feats(synth.peak())
    for other in (synth.two_peaks(), synth.edge_drop(), synth.ramp()):
        assert shape.similarity(a, feats(other))["score"] < 0.6


def test_scale_ratio_is_reported_not_mixed_into_the_score():
    a = feats(synth.peak())
    b = feats(synth.peak(), gain=4.0)
    sim = shape.similarity(a, b)
    assert sim["score"] > 0.99
    assert sim["scale"] == pytest.approx(4.0, rel=1e-6)


def test_snapshot_keeps_values_before_normalisation():
    f = feats(synth.peak(), gain=3.0, offset=100.0)
    ch = f["channels"]["A"]
    assert len(ch["path"]) == shape.PATH_N
    assert len(ch["grid"]) == shape.GRID_N ** 2
    assert ch["stats"]["min"] == pytest.approx(100.0, abs=1e-3)
    assert max(ch["path"]) > 100.0


def test_blank_and_none_are_dropped_not_read_as_zero():
    s = synth.scan(synth.ramp(), cols=10, rows=10, channels=("A",))
    s["value"]["A"][0] = ""
    s["value"]["A"][1] = None
    s["value"]["A"][2] = "12.5"
    f = shape.record_features(s)
    assert f["channels"]["A"]["stats"]["n"] == 98
    assert shape.num("12.5") == 12.5 and shape.num("") is None and shape.num(True) is None


def test_flat_matches_flat_and_nothing_else():
    flat = feats(lambda x, z: 0.0)
    assert shape.similarity(flat, feats(lambda x, z: 0.0))["score"] == pytest.approx(1.0)
    assert shape.similarity(flat, feats(synth.peak()))["score"] == pytest.approx(0.0)


def test_a_line_scan_has_no_2d_and_compares_by_1d_only():
    line = {"value": {"A": [float(i % 7) for i in range(200)]},
            "xyz": {"x": [float(i) for i in range(200)], "z": [0.0] * 200}}
    f = shape.record_features(line)
    assert f["channels"]["A"]["grid"] is None
    sim = shape.similarity(f, f)
    assert sim["d2"] is None and sim["d1"] == pytest.approx(1.0)


def test_no_common_channel_means_no_comparison():
    a = feats(synth.peak(), channels=("A",))
    b = shape.record_features({"value": {"Z": [1.0, 2.0, 3.0]}, "xyz": {"x": [0, 1, 2], "z": [0, 1, 2]}})
    assert shape.similarity(a, b) is None


def test_a_short_scan_is_interpolated_to_full_length():
    f = shape.record_features({"value": {"A": [0.0, 1.0]}, "xyz": {"x": [0, 1], "z": [0, 1]}})
    path = f["channels"]["A"]["path"]
    assert len(path) == shape.PATH_N and path[0] == 0.0 and path[-1] == 1.0


# ─── 단계별 제안 ────────────────────────────────────────────────────────────

TREE_ROWS = [
    {"id": 1, "parent_id": None, "level": 1, "name": "신호 이상", "retired": False, "case_id": None},
    {"id": 11, "parent_id": 1, "level": 2, "name": "피크", "retired": False, "case_id": None},
    {"id": 111, "parent_id": 11, "level": 3, "name": "단일 피크", "retired": False, "case_id": 7},
    {"id": 112, "parent_id": 11, "level": 3, "name": "이중 피크", "retired": False, "case_id": None},
    {"id": 12, "parent_id": 1, "level": 2, "name": "레벨 저하", "retired": False, "case_id": 12},
    {"id": 2, "parent_id": None, "level": 1, "name": "위치 이상", "retired": False, "case_id": None},
    {"id": 21, "parent_id": 2, "level": 2, "name": "기울기", "retired": False, "case_id": None},
]


def cand(fn, label_ids, **kw):
    return {"features": feats(fn, **kw), "label_ids": label_ids, "scan_enum": "S"}


def test_a_leaf_label_also_votes_for_its_ancestors():
    tree = Tree(TREE_ROWS)
    res = shape.rank(feats(synth.peak()), [cand(synth.peak(), [111], gain=2.0),
                                           cand(synth.ramp(), [21])], tree)
    by_level = {lvl: [e["label_id"] for e in row] for lvl, row in res["levels"].items()}
    assert by_level[1][0] == 1 and by_level[2][0] == 11 and by_level[3][0] == 111
    assert res["suggestion"]["label_id"] == 111 and res["suggestion"]["level"] == 3
    assert res["suggestion"]["case_id"] == 7


def test_a_split_leaf_stops_the_suggestion_at_the_middle_level():
    tree = Tree(TREE_ROWS)
    target = feats(synth.peak())
    # 같은 모양에 서로 다른 소분류가 붙어 있다 → 소분류 1 · 2위가 붙어 갈림
    res = shape.rank(target, [cand(synth.peak(), [111], gain=2.0),
                              cand(synth.peak(), [112], gain=3.0)], tree)
    assert res["levels"][2][0]["suggested"] is True
    assert not any(e["suggested"] for e in res["levels"][3])
    assert res["suggestion"]["label_id"] == 11 and res["suggestion"]["level"] == 2


def test_nothing_is_suggested_below_an_unsure_level():
    tree = Tree(TREE_ROWS)
    res = shape.rank(feats(synth.peak()), [cand(synth.ramp(), [21]), cand(synth.edge_drop(), [12])],
                     tree)
    assert res["suggestion"] is None
    assert all(not e["suggested"] for row in res["levels"].values() for e in row)


def test_a_label_stopped_at_the_middle_level_carries_its_own_case():
    tree = Tree(TREE_ROWS)
    res = shape.rank(feats(synth.edge_drop()), [cand(synth.edge_drop(), [12], gain=2.0)], tree)
    assert res["suggestion"]["label_id"] == 12 and res["suggestion"]["case_id"] == 12


def test_prepared_features_score_exactly_like_raw_snapshots():
    pairs = [(feats(synth.peak()), feats(synth.peak(), cols=90, gain=3.0)),
             (feats(synth.peak()), feats(synth.ramp())),
             (feats(lambda x, z: 0.0), feats(lambda x, z: 0.0)),
             (feats(synth.peak(), cols=30, rows=20), feats(synth.peak(), cols=60, rows=40))]
    for a, b in pairs:
        raw, fast = shape.similarity(a, b), shape.similarity(shape.prepare(a), shape.prepare(b))
        for k in ("score", "d1", "d2", "scale"):
            if raw[k] is None:
                assert fast[k] is None
            else:
                assert fast[k] == pytest.approx(raw[k], abs=1e-12)


# ─── 유사 사례 ──────────────────────────────────────────────────────────────

def test_similar_cases_are_grouped_by_case_and_ignore_unlike_records():
    tree = Tree(TREE_ROWS)
    res = shape.rank(feats(synth.peak()), [cand(synth.peak(), [111], gain=2.0),
                                           cand(synth.peak(), [111], gain=3.0),
                                           cand(synth.edge_drop(), [12])], tree)
    assert [c["case_id"] for c in res["cases"]] == [7]           # 레벨 저하(사례 12)는 닮지 않았다
    assert res["cases"][0]["count"] == 2 and res["cases"][0]["score"] > 0.95
    assert res["cases"][0]["paths"] == ["신호 이상 › 피크 › 단일 피크"]


# ─── 묶음 ──────────────────────────────────────────────────────────────────

def item(i, fn, scan_enum="S", **kw):
    return {"id": i, "features": feats(fn, **kw), "scan_enum": scan_enum}


def test_clusters_group_like_shapes_and_leave_the_rest_unclustered():
    items = ([item(i, synth.peak(), gain=1.0 + i) for i in range(1, 5)]
             + [item(i, synth.edge_drop(), offset=float(i)) for i in range(10, 13)]
             + [item(20, synth.ramp()), item(21, synth.two_peaks())])
    out = shape.cluster(items)
    groups = [sorted(m["id"] for m in c["members"]) for c in out["clusters"]]
    assert groups == [[1, 2, 3, 4], [10, 11, 12]]                # 큰 묶음 먼저
    assert sorted(out["unclustered"]) == [20, 21]
    first = out["clusters"][0]
    assert first["members"][0]["representative"] is True and first["members"][0]["score"] == 1.0
    assert all(m["score"] >= shape.CLUSTER_MIN for m in first["members"])


def test_clusters_never_mix_scan_kinds():
    items = ([item(i, synth.peak(), scan_enum="A") for i in range(1, 3)]
             + [item(i, synth.peak(), scan_enum="B") for i in range(3, 5)])
    out = shape.cluster(items)                                    # 종류마다 2개뿐 — 묶음이 못 된다
    assert out["clusters"] == [] and sorted(out["unclustered"]) == [1, 2, 3, 4]
    out = shape.cluster(items + [item(5, synth.peak(), scan_enum="A")])
    assert [c["scan_enum"] for c in out["clusters"]] == ["A"]
    assert sorted(m["id"] for m in out["clusters"][0]["members"]) == [1, 2, 5]


def test_cluster_category_is_the_deepest_level_the_labelled_members_agree_on():
    tree = Tree(TREE_ROWS)
    # 셋 중 둘이 단일 피크, 하나는 이중 피크 → 중분류(피크)는 3/3 합의, 소분류는 2/3 ≥ 0.6
    got = shape.cluster_category([[111], [111], [112], []], tree)
    assert got["labelled"] == 3 and got["unlabelled"] == 1
    assert got["category"]["label_id"] == 111 and got["category"]["level"] == 3
    assert got["category"]["case_id"] == 7
    # 반반으로 갈리면 소분류는 합의가 아니다 — 중분류에서 멈춘다
    got = shape.cluster_category([[111], [112]], tree)
    assert got["category"]["label_id"] == 11 and got["category"]["level"] == 2
    assert not any(e["agreed"] for e in got["levels"][3])
    # 라벨된 구성원이 없으면 분류도 없다
    assert shape.cluster_category([[], []], tree)["category"] is None


def test_leave_one_out_counts_a_level_only_for_labels_that_reach_it():
    tree = Tree(TREE_ROWS)
    items = [cand(synth.peak(), [111]), cand(synth.peak(), [111], gain=2.0),
             cand(synth.ramp(), [21]), cand(synth.ramp(), [21], gain=0.5)]
    out = shape.evaluate(items, tree)
    assert out[1]["n"] == 4 and out[1]["top1"] == 4
    assert out[3]["n"] == 2 and out[3]["top1"] == 2      # 위치 이상 › 기울기 는 소분류가 없다


# ─── 묶음 지도 ──────────────────────────────────────────────────────────────

def test_the_map_puts_like_shapes_together_and_unlike_ones_apart():
    items = ([item(i, synth.peak(), gain=1.0 + i) for i in range(1, 6)]
             + [item(i, synth.edge_drop(), offset=float(i)) for i in range(10, 15)])
    out = shape.cluster_map(items)
    pts = out["points"]
    assert set(pts) == {it["id"] for it in items}

    def gap(a, b):
        return ((pts[a][0] - pts[b][0]) ** 2 + (pts[a][1] - pts[b][1]) ** 2) ** 0.5
    within = max(gap(1, j) for j in range(2, 6))
    across = min(gap(i, j) for i in range(1, 6) for j in range(10, 15))
    assert across > 3 * within


def test_a_record_outside_the_run_lands_where_its_twin_is():
    items = ([item(i, synth.peak(), gain=1.0 + i) for i in range(1, 6)]
             + [item(i, synth.ramp(), offset=float(i)) for i in range(10, 15)])
    out = shape.cluster_map(items)
    lms = {it["id"]: it["features"] for it in items}
    # 결과에 없던 「피크」 하나 — 피크들 곁에 놓인다
    x, y = shape.project_features(out["proj"], feats(synth.peak(), gain=9.0),
                                  [lms[i] for i in out["proj"]["landmarks"]])
    near_peak = min(((x - out["points"][i][0]) ** 2 + (y - out["points"][i][1]) ** 2) for i in range(1, 6))
    near_ramp = min(((x - out["points"][i][0]) ** 2 + (y - out["points"][i][1]) ** 2) for i in range(10, 15))
    assert near_peak < near_ramp
    # 기준점 자신을 다시 투영하면 제 자리다
    lm = out["proj"]["landmarks"][0]
    again = shape.project_features(out["proj"], lms[lm], [lms[i] for i in out["proj"]["landmarks"]])
    assert again == pytest.approx(out["points"][lm], abs=1e-4)


def test_a_tiny_group_has_no_map():
    out = shape.cluster_map([item(1, synth.peak()), item(2, synth.peak())])
    assert out["proj"] is None and out["points"] == {1: (0.0, 0.0), 2: (0.0, 0.0)}
