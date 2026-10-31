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


def test_a_2d_scan_compares_by_x_and_z_profiles_not_by_scan_order():
    # 행 수가 다른 raster 는 측정 순서가 다른 경로다 — 그 순서로 편 곡선은 뭉개지지만 단면은 순서와 무관하다.
    sim = shape.similarity(feats(synth.peak(), cols=30, rows=20), feats(synth.peak(), cols=60, rows=40))
    assert sim["d2"] > 0.97
    assert sim["d1"] > 0.97
    ch = sim["by_channel"]["A"]
    assert ch["dx"] > 0.97 and ch["dz"] > 0.97


def test_x_profile_z_profile_and_grid_are_normalised_each_on_its_own():
    # Z 기울기가 1000 배 큰 기록과 3 배인 기록 — 한꺼번에 정규화하면 X 의 봉우리가 기울기에 묻힌다.
    bump = synth.peak(cz=0.5, w=0.1)
    a = feats(lambda x, z: 1000.0 * z + bump(x, 0.5))
    b = feats(lambda x, z: 3.0 * z + bump(x, 0.5))
    ch = shape.similarity(a, b)["by_channel"]["A"]
    assert ch["dx"] > 0.99 and ch["dz"] > 0.99
    assert ch["d2"] < ch["d1"]
    prof = shape.normalized_profiles(b, ["A"], grid=True)["A"]
    assert max(prof["x"]) > 1.0                       # X 단면은 제 범위로 펴져 봉우리가 보인다
    assert len(prof["x"]) == len(prof["z"]) == shape.GRID_N and len(prof["grid"]) == shape.GRID_N ** 2


def test_a_line_scan_and_an_area_scan_are_not_compared():
    line = {"value": {"A": [float(i % 7) for i in range(200)]},
            "xyz": {"x": [float(i) for i in range(200)], "z": [0.0] * 200}}
    assert shape.similarity(shape.record_features(line), feats(synth.peak(), channels=("A",))) is None
    assert shape.normalized_profiles(shape.record_features(line))["A"].keys() == {"line"}


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

def item(i, fn, scan_enum="S", sensor_id="", chuck_id="", **kw):
    return {"id": i, "features": feats(fn, **kw), "scan_enum": scan_enum, "chuck_id": chuck_id,
            "sensor_id": sensor_id}


def skewed(dev, *, cols=30, rows=20, gain=1.0):
    """채널 셋(tr · txh · tyh)이 같은 바탕(기울기)을 따르고, txh 만 `dev(x, z)` 만큼 어긋난 면 스캔."""
    xs, zs, tr, txh, tyh = [], [], [], [], []
    for r in range(rows):
        for c in range(cols):
            x, z = c / (cols - 1), r / (rows - 1)
            base = x + 0.5 * z
            xs.append(x * 10.0)
            zs.append(z * 4.0)
            tr.append(gain * base)
            txh.append(3.0 * gain * (base + dev(x, z)) + 100.0)       # 단위 · offset 이 달라도
            tyh.append(-0.2 * gain * base)                            # 부호가 반대여도 — 정규화 후 같은 바탕
    return shape.record_features({"value": {"tr": tr, "txh": txh, "tyh": tyh}, "xyz": {"x": xs, "z": zs}})


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


def test_clusters_never_mix_sensors_of_one_scan_kind():
    # 같은 스캔 종류 · 같은 모양이어도 센서가 다르면 다른 범위다 (SL-CLU-3)
    items = ([item(i, synth.peak(), sensor_id="1", gain=1.0 + i) for i in range(1, 4)]
             + [item(i, synth.peak(), sensor_id="2", gain=1.0 + i) for i in range(4, 6)])
    out = shape.cluster(items)
    assert [(c["scan_enum"], c["sensor_id"]) for c in out["clusters"]] == [("S", "1")]
    assert sorted(m["id"] for m in out["clusters"][0]["members"]) == [1, 2, 3]
    assert sorted(out["unclustered"]) == [4, 5]                  # 센서 2 는 2건뿐 — 섞이지 않는다
    assert shape.group_key("S", "", "1") != shape.group_key("S", "", "2") != shape.group_key("S", "", "")


def test_clusters_never_mix_chucks_of_one_scan_kind_and_sensor():
    # 같은 스캔 종류 · 센서 · 모양이어도 척이 다르면 다른 범위다 (SL-CLU-3)
    items = ([item(i, synth.peak(), chuck_id="CH1", sensor_id="1", gain=1.0 + i) for i in range(1, 4)]
             + [item(i, synth.peak(), chuck_id="CH2", sensor_id="1", gain=1.0 + i) for i in range(4, 6)])
    out = shape.cluster(items)
    assert [(c["scan_enum"], c["chuck_id"], c["sensor_id"]) for c in out["clusters"]] == [("S", "CH1", "1")]
    assert sorted(out["unclustered"]) == [4, 5]
    assert shape.group_key("S", "CH1", "1") != shape.group_key("S", "CH2", "1")
    assert shape.group_of({"scan_enum": "S", "chuck_id": None, "sensor_id": 2}) == ("S", "", "2")


# ─── 채널 간 편차 (SL-CLU-9) ────────────────────────────────────────────────

RIGHT = lambda x, z: 0.4 if x > 0.75 else 0.0                    # txh 가 오른쪽 끝에서 들뜬다
LEFT = lambda x, z: 0.4 if x < 0.25 else 0.0                     # txh 가 왼쪽 끝에서 들뜬다
NONE = lambda x, z: 0.0


def scope():
    """한 묶음 범위 — 평소 기록 다섯 + 오른쪽 어긋남 셋 + 왼쪽 어긋남 셋. tyh 는 늘 반대로 움직인다."""
    return ([("n", skewed(NONE, gain=1.0 + i)) for i in range(5)]
            + [("r", skewed(RIGHT, gain=1.0 + i, cols=30 + 10 * i)) for i in range(3)]
            + [("l", skewed(LEFT, gain=1.0 + i)) for i in range(3)])


def test_skew_compares_where_channels_part_not_the_shared_shape():
    recs = scope()
    base = shape.skew_baseline(f for _, f in recs)
    p = {k + str(i): shape.prepare_skew(f, base) for i, (k, f) in enumerate(recs)}
    assert shape.skew_similarity(p["r5"], p["r6"])["score"] > 0.9
    assert shape.skew_similarity(p["n0"], p["n1"])["score"] > 0.9     # 평소끼리 — 어긋남이 없다
    assert shape.skew_similarity(p["r5"], p["l8"])["score"] < 0.3     # 바탕은 같아도 어긋나는 자리가 다르다
    assert shape.skew_similarity(p["r5"], p["n0"])["score"] < shape.CLUSTER_MIN - 0.1   # 좁은 어긋남은 바닥에 묽어진다
    assert shape.similarity(recs[5][1], recs[8][1])["score"] > 0.8    # 모양 유사도로는 가르지 못한다
    assert shape.skew_similarity(p["r5"], p["l8"])["channels"] == ["tr", "txh", "tyh"]


def test_the_baseline_takes_away_the_usual_offset_between_channels():
    # tyh 가 늘 반대로 움직이면 그 고정된 편차가 모든 기록에 실린다 — 기준선이 없으면 어긋남이 묻힌다
    recs = scope()
    assert shape.skew_similarity(recs[5][1], recs[8][1])["score"] > 0.8
    base = shape.skew_baseline(f for _, f in recs)
    assert base["keys"] == ["tr", "txh", "tyh"] and len(base["mats"]["x"]) == 3 * shape.GRID_N
    prof = shape.skew_profiles(recs[5][1], base)
    assert max(prof["txh"]["x"]) > 0.2 and abs(prof["txh"]["x"][0]) < 0.05   # 오른쪽 끝만 들떠 있다
    assert shape.skew_baseline(f for _, f in recs[:2]) is None                 # 너무 적으면 기준선이 없다


def test_skew_needs_two_common_channels_and_uses_only_the_common_ones():
    one = feats(synth.peak(), channels=("A",))
    assert shape.skew_similarity(one, one) is None
    assert shape.skew_profiles(one) == {}
    a = skewed(RIGHT)
    drop = {"version": a["version"], "channels": {k: v for k, v in a["channels"].items() if k != "tyh"}}
    assert shape.skew_similarity(a, drop)["channels"] == ["tr", "txh"]
    prof = shape.skew_profiles(a)
    assert prof.keys() == {"tr", "txh", "tyh"} and len(prof["txh"]["x"]) == shape.GRID_N
    assert abs(sum(prof[k]["x"][-1] for k in prof)) < 0.01     # 자리마다 채널 편차의 합은 0 — 채널 평균에서 잰다


def horvert(pass_dev, *, cols=24, rows=16, gain=1.0):
    """HORVERT 처럼 같은 면을 두 번 지나는 스캔 — 가로 raster 한 번, 세로 raster 한 번. txh 는 `pass_dev[p]`
    만큼(p = 0 가로, 1 세로) 오른쪽 절반에서 어긋난다. 칸 · 단면에서는 두 번 지난 점이 한 칸에 섞인다."""
    xs, zs, tr, txh, tyh = [], [], [], [], []
    walk = ([(c, r, 0) for r in range(rows) for c in range(cols)]
            + [(c, r, 1) for c in range(cols) for r in range(rows)])
    for c, r, p in walk:
        x, z = c / (cols - 1), r / (rows - 1)
        base = x + 0.5 * z
        xs.append(x * 10.0)
        zs.append(z * 4.0)
        tr.append(gain * base)
        txh.append(3.0 * gain * (base + (pass_dev[p] if x > 0.5 else 0.0)) + 100.0)
        tyh.append(-0.2 * gain * base)
    return shape.record_features({"value": {"tr": tr, "txh": txh, "tyh": tyh}, "xyz": {"x": xs, "z": zs}})


def test_an_area_scans_skew_also_reads_the_scan_order_curve():
    # 가로에서만 어긋난 기록과 세로에서만 어긋난 기록은 같은 칸에 같은 만큼 어긋난다 — 칸 · 단면으로는 못 가른다.
    # 측정 순서 곡선에서는 어긋나는 때가 앞 절반 · 뒤 절반으로 갈린다 (SL-CLU-11).
    recs = ([horvert((0.0, 0.0), gain=1.0 + 0.02 * i) for i in range(5)]
            + [horvert((0.4, 0.0), gain=1.0 + 0.02 * i) for i in range(3)]
            + [horvert((0.0, 0.4), gain=1.0 + 0.02 * i) for i in range(3)])  # 주성분은 진폭도 재료 — gain 은 거의 같게
    base = shape.skew_baseline(recs)
    assert len(base["mats"]["path"]) == 3 * shape.PATH_N
    p = [shape.prepare_skew(f, base) for f in recs]
    h_v = shape.skew_similarity(p[5], p[8])
    assert h_v["d2"] > 0.9 and h_v["d1"] > 0.9                     # 칸 · 단면은 같다고 본다
    assert h_v["dp"] < 0.3 and h_v["score"] < shape.CLUSTER_MIN    # 측정 순서가 가른다
    assert shape.skew_similarity(p[5], p[6])["score"] > 0.9
    prof = shape.skew_profiles(recs[5], base)
    assert len(prof["txh"]["line"]) == shape.PATH_N and len(prof["txh"]["x"]) == shape.GRID_N
    items = [{"id": i, "features": f, "scan_enum": "S"} for i, f in enumerate(recs, start=1)]
    for method in shape.METHODS:
        out = shape.cluster(items, method)
        assert sorted(sorted(m["id"] for m in c["members"]) for c in out["clusters"]) == \
            [[1, 2, 3, 4, 5], [6, 7, 8], [9, 10, 11]], method


def test_a_stored_baseline_or_model_without_the_scan_order_curve_still_reads():
    # SL-CLU-11 전에 저장된 기준선 · 투영식(면 스캔에 측정 순서 곡선이 없다)도 다시 묶기 전까지 읽힌다
    recs = [f for _, f in pca_scope()]
    base = shape.skew_baseline(recs)
    old = {**base, "mats": {m: v for m, v in base["mats"].items() if m != "path"}}
    assert shape.prepare_skew(recs[5], old)["centred"] is False
    assert shape.skew_profiles(recs[5], old)["txh"]["x"] == shape.skew_profiles(recs[5])["txh"]["x"]
    model = shape.pca_model(recs)
    keep = [i for i, b in enumerate(model["layout"]) if b["mat"] != "path"]
    pos, cols = 0, []
    for b in model["layout"]:
        if b["mat"] != "path":
            cols += range(pos, pos + b["len"])
        pos += b["len"]
    legacy = {**model, "layout": [model["layout"][i] for i in keep], "factor": [model["factor"][i] for i in keep],
              "median": [model["median"][i] for i in cols], "mean": [model["mean"][i] for i in cols],
              "components": [[c[i] for i in cols] for c in model["components"]]}
    c = shape.pca_coords(legacy, recs[5])
    assert c is not None and len(c) == len(model["components"])
    assert shape.pca_parts(legacy, recs[5])["distance"] > 0


def test_prepared_skew_scores_exactly_like_raw_snapshots():
    a, b = skewed(lambda x, z: 0.3 * x), skewed(lambda x, z: 0.2 * x * x, gain=2.0)
    raw = shape.skew_similarity(a, b)
    assert shape.skew_similarity(shape.prepare_skew(a), shape.prepare_skew(b)) == raw


def test_clusters_group_by_where_channels_part():
    items = [{"id": i, "features": f, "scan_enum": "S"} for i, (_, f) in enumerate(scope(), start=1)]
    out = shape.cluster(items)
    assert sorted(sorted(m["id"] for m in c["members"]) for c in out["clusters"]) == \
        [[1, 2, 3, 4, 5], [6, 7, 8], [9, 10, 11]]
    m = shape.cluster_map(items)
    assert m["proj"]["baseline"]["keys"] == ["tr", "txh", "tyh"]


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


# ─── 주성분 (SL-CLU-10) ─────────────────────────────────────────────────────

def pca_scope():
    """`scope()` 와 같은 어긋남 — 다만 진폭(gain)이 거의 같다. 주성분은 진폭도 재료라 gain 이 크게 다르면 그것으로 갈린다."""
    return ([("n", skewed(NONE, gain=1.0 + 0.02 * i)) for i in range(5)]
            + [("r", skewed(RIGHT, gain=1.0 + 0.02 * i, cols=30 + 10 * i)) for i in range(3)]
            + [("l", skewed(LEFT, gain=1.0 + 0.02 * i)) for i in range(3)])


def test_pca_groups_by_where_channels_part_and_places_records_on_the_axes():
    items = [{"id": i, "features": f, "scan_enum": "S"} for i, (_, f) in enumerate(pca_scope(), start=1)]
    model = shape.pca_model(it["features"] for it in items)
    assert model["kind"] == "area" and model["keys"] == ["tr", "txh", "tyh"] and len(model["components"]) >= 2
    out = shape.cluster(items, "pca")
    assert sorted(sorted(m["id"] for m in c["members"]) for c in out["clusters"]) ==         [[1, 2, 3, 4, 5], [6, 7, 8], [9, 10, 11]]
    m = shape.cluster_map(items, "pca")
    assert set(m["points"]) == {it["id"] for it in items} and m["proj"]["baseline"]["keys"] == ["tr", "txh", "tyh"]
    # 결과에 없던 기록도 투영식만으로 같은 자리에 놓인다 — 정확한 선형 투영
    assert shape.project_features(m["proj"], items[5]["features"]) == pytest.approx(m["points"][6], abs=1e-4)
    axes = shape.pca_axes(m["proj"])
    assert [a["axis"] for a in axes["axes"]] == [1, 2] and 0 < axes["explained"] <= 1
    assert axes["axes"][0]["parts"][0]["channel"] == "txh"          # 갈리는 것은 txh 의 어긋남이다
    parts = shape.pca_parts(m["proj"], items[5]["features"])
    assert parts["distance"] > shape.pca_parts(m["proj"], items[0]["features"])["distance"]


def test_pca_scores_like_skew_and_needs_the_scopes_channel_set():
    recs = [f for _, f in pca_scope()]
    model = shape.pca_model(recs)
    c = [shape.pca_coords(model, f) for f in recs]
    assert shape.pca_similarity(c[5], c[6]) > shape.CLUSTER_MIN
    assert shape.pca_similarity(c[5], c[8]) < 0.3                    # 바탕은 같아도 어긋나는 자리가 다르다
    assert shape.pca_similarity(c[5], None) is None
    drop = {"version": recs[0]["version"], "channels": {k: v for k, v in recs[0]["channels"].items() if k != "tyh"}}
    assert shape.pca_coords(model, drop) is None                    # 채널 집합이 다르면 이 범위의 주성분에 없다
    assert shape.pca_model(recs[:2]) is None
    with pytest.raises(ValueError):
        shape.check_method("pcaa")
