"""HTTP 왕복 — 실제 PostgreSQL + 가짜 fleet (README.api.md · SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from scanlabel.application.usecases import run_eval, seed_labels
from scanlabel.data.repository import Store
from scanlabel.processing import shape

from . import synth

SEED = Path(__file__).resolve().parents[1] / "labels.seed.example.json"
SRC1 = "f1.tdf::run_log1_IFXAxDATA_ARR_PHYS_SCAN_RAW_DATA_STRUCT.dd"
SRC2 = "f1.tdf::run_log2_IFXAxDATA_ARR_PHYS_SCAN_RAW_DATA_STRUCT.dd"


def file_of(*scans):
    out = []
    for s in scans:
        out += [s, synth.meta(s["source"])]
    return out


def seed(dsn) -> dict[str, int]:
    store = Store(dsn)
    try:
        seed_labels(store, SEED)
        return {d["name"]: d["id"] for d in store.list_defs()}
    finally:
        store.close()


# ─── 라벨 트리 ──────────────────────────────────────────────────────────────

def test_seed_is_idempotent_and_builds_three_levels(client, dsn):
    ids = seed(dsn)
    again = seed(dsn)
    assert ids == again
    defs = {d["name"]: d for d in client.get("/label-defs").json()["defs"]}
    assert defs["단일 피크"]["path"] == "신호 이상 › 피크 › 단일 피크"
    assert defs["단일 피크"]["level"] == 3 and defs["단일 피크"]["code"] == "A-01-1"
    assert defs["결측 · 잘림"]["level"] == 2          # 중분류에서 끝나는 가지
    assert defs["신호 이상"]["origin"] == "seed"


def test_csv_seed_attaches_code_to_the_deepest_node(dsn, tmp_path):
    p = tmp_path / "tree.csv"
    p.write_text("대분류,중분류,소분류,코드,설명,사례번호\n"
                 "신호 이상,피크,단일 피크,A-01-1,한 자리,7\n"
                 "신호 이상,피크,이중 피크,A-01-2,,\n"
                 "신호 이상,레벨 저하,,A-02,,\n", encoding="utf-8")
    store = Store(dsn)
    try:
        assert seed_labels(store, p) == {"created": 5, "existing": 0}
        defs = {d["name"]: d for d in store.list_defs()}
    finally:
        store.close()
    assert defs["단일 피크"]["case_id"] == 7 and defs["레벨 저하"]["code"] == "A-02"
    assert defs["피크"]["code"] == ""


def test_tree_rules_are_enforced(client, dsn):
    ids = seed(dsn)
    # 소분류 아래에는 만들 수 없다
    r = client.post("/label-defs", json={"parent_id": ids["단일 피크"], "name": "더 아래"})
    assert r.status_code == 422 and "소분류 아래" in r.json()["detail"]
    # 같은 부모 아래 이름 중복 — 대소문자 · 공백 무시
    r = client.post("/label-defs", json={"parent_id": ids["신호 이상"], "name": "  피크 "})
    assert r.status_code == 422 and "같은 이름" in r.json()["detail"]
    # 다른 부모 아래 같은 이름은 된다
    r = client.post("/label-defs", json={"parent_id": ids["위치 이상"], "name": "피크"})
    assert r.status_code == 201
    made = r.json()
    assert made["level"] == 2 and made["origin"] == "added"
    assert made["path"] == "위치 이상 › 피크"
    # 다른 단계의 부모로 옮기기는 422, 같은 단계는 된다
    r = client.put(f"/label-defs/{ids['단일 피크']}", json={"parent_id": ids["신호 이상"]})
    assert r.status_code == 422
    r = client.put(f"/label-defs/{ids['단일 피크']}", json={"parent_id": made["id"]})
    assert r.status_code == 200 and r.json()["path"] == "위치 이상 › 피크 › 단일 피크"
    assert client.put("/label-defs/999999", json={"name": "x"}).status_code == 404


def test_retiring_hides_the_node_and_its_children(client, dsn):
    ids = seed(dsn)
    assert client.put(f"/label-defs/{ids['피크']}", json={"retired": True}).status_code == 200
    names = {d["name"] for d in client.get("/label-defs").json()["defs"]}
    assert "피크" not in names and "단일 피크" not in names and "신호 이상" in names
    every = {d["name"]: d for d in client.get("/label-defs?include_retired=true").json()["defs"]}
    assert every["단일 피크"]["retired_effective"] is True and every["단일 피크"]["retired"] is False


# ─── 기록 라벨 ──────────────────────────────────────────────────────────────

def test_label_round_trip_and_rename_follows(client, dsn, fleet):
    ids = seed(dsn)
    fleet.put(1, "f1.tdf", file_of(synth.scan(synth.peak(), source=SRC1)))
    r = client.put("/labels", json={"server_id": 1, "name": "f1.tdf", "source": SRC1,
                                    "label_ids": [ids["단일 피크"]]})
    assert r.status_code == 200
    row = r.json()["label"]
    assert row["servername"] == "scanner-a" and row["sig"] == "20260811T005720-100"
    assert row["scan_enum"] == "FINEHORVERT_SCAN" and row["meta"]["chuck_id"] == "CH1"
    assert row["labels"][0]["path"] == "신호 이상 › 피크 › 단일 피크"
    # fleet 은 캐시로만 불린다 — FTP 를 일으키지 않는다
    assert all(b.get("source") == "cache" for m, p, b in fleet.calls if m == "POST")

    client.put(f"/label-defs/{ids['단일 피크']}", json={"name": "외곬 피크"})
    got = client.get("/labels", params={"server_id": 1, "name": "f1.tdf"}).json()["labels"]
    assert got[0]["labels"][0]["path"] == "신호 이상 › 피크 › 외곬 피크"
    assert "features" not in got[0]

    counts = {d["name"]: d for d in client.get("/label-defs").json()["defs"]}
    assert counts["외곬 피크"]["count"] == 1 and counts["신호 이상"]["count_total"] == 1
    assert counts["신호 이상"]["count"] == 0

    r = client.put("/labels", json={"server_id": 1, "name": "f1.tdf", "source": SRC1, "label_ids": []})
    assert r.json() == {"label": None}
    assert client.get("/labels", params={"server_id": 1, "name": "f1.tdf"}).json()["labels"] == []


def test_label_validation(client, dsn, fleet):
    ids = seed(dsn)
    fleet.put(1, "f1.tdf", file_of(synth.scan(synth.peak(), source=SRC1)))
    body = {"server_id": 1, "name": "f1.tdf", "source": SRC1}
    r = client.put("/labels", json={**body, "label_ids": [ids["피크"], ids["단일 피크"]]})
    assert r.status_code == 422 and "조상과 자손" in r.json()["detail"]
    client.put(f"/label-defs/{ids['위치 이상']}", json={"retired": True})
    assert client.put("/labels", json={**body, "label_ids": [ids["X 방향 이탈"]]}).status_code == 422
    assert client.put("/labels", json={**body, "label_ids": [424242]}).status_code == 422
    # 그 파일에 없는 기록 → 404, fleet 캐시에 없는 파일 → 422, 모르는 서버 → 404
    assert client.put("/labels", json={**body, "source": "nope", "label_ids": [ids["피크"]]}).status_code == 404
    r = client.put("/labels", json={**body, "name": "cold.tdf", "label_ids": [ids["피크"]]})
    assert r.status_code == 422 and "먼저" in r.json()["detail"]
    assert client.put("/labels", json={**body, "server_id": 9, "label_ids": [ids["피크"]]}).status_code == 404


def test_fleet_down_is_502(client, dsn, fleet):
    seed(dsn)
    fleet.down = True
    r = client.get("/labels", params={"server_id": 1, "name": "f1.tdf"})
    assert r.status_code == 502
    assert client.get("/health").json()["fleet"] is False


# ─── 유사 · 제안 ────────────────────────────────────────────────────────────

def label(client, server_id, name, source, label_id):
    r = client.put("/labels", json={"server_id": server_id, "name": name, "source": source,
                                    "label_ids": [label_id]})
    assert r.status_code == 200, r.text


def test_similar_ranks_by_shape_and_suggests_by_level(client, dsn, fleet):
    ids = seed(dsn)
    fleet.put(2, "old1.tdf", file_of(synth.scan(synth.peak(), gain=3.0, source="old1::a")))
    fleet.put(2, "old2.tdf", file_of(synth.scan(synth.edge_drop(), source="old2::a")))
    fleet.put(2, "old3.tdf", file_of(synth.scan(synth.ramp(), source="old3::a")))
    label(client, 2, "old1.tdf", "old1::a", ids["단일 피크"])
    label(client, 2, "old2.tdf", "old2::a", ids["가장자리 저하"])
    label(client, 2, "old3.tdf", "old3::a", ids["X 방향 이탈"])

    fleet.put(1, "new.tdf", file_of(synth.scan(synth.peak(), cols=90, offset=50.0, source=SRC1),
                                    synth.scan(synth.ramp(), source=SRC2)))
    r = client.post("/labels/similar", json={"server_id": 1, "name": "new.tdf", "source": SRC1})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["matches"][0]["source"] == "old1::a"
    assert res["matches"][0]["similarity"]["score"] > 0.95
    assert res["matches"][0]["similarity"]["scale"] is not None
    assert set(res["matches"][0]["profiles"]) == {"A", "B"} and len(res["target_profiles"]["A"]["x"]) == 16
    assert res["suggestion"]["path"] == "신호 이상 › 피크 › 단일 피크"
    assert res["suggestion"]["case_id"] == 1
    assert res["levels"]["1"][0]["suggested"] is True

    # source 없이 → 파일의 기록마다 제안 1건
    whole = client.post("/labels/similar", json={"server_id": 1, "name": "new.tdf"}).json()
    got = {r["source"]: r["suggestion"] for r in whole["records"]}
    assert got[SRC1]["label_id"] == ids["단일 피크"]
    assert got[SRC2]["label_id"] == ids["X 방향 이탈"]


def test_similar_excludes_itself_and_respects_scan_kind(client, dsn, fleet):
    ids = seed(dsn)
    other = synth.scan(synth.peak(), source="o::a")
    fleet.put(2, "o.tdf", [other, synth.meta("o::a", scan_enum="COARSEHORVERT_SCAN")])
    label(client, 2, "o.tdf", "o::a", ids["단일 피크"])
    fleet.put(1, "f1.tdf", file_of(synth.scan(synth.peak(), source=SRC1)))
    label(client, 1, "f1.tdf", SRC1, ids["이중 피크"])

    body = {"server_id": 1, "name": "f1.tdf", "source": SRC1}
    same = client.post("/labels/similar", json=body).json()
    assert same["matches"] == [] and same["suggestion"] is None     # 자기 자신은 빼고, 종류가 다른 것도 뺀다
    every = client.post("/labels/similar", json={**body, "same_scan_only": False}).json()
    assert [m["source"] for m in every["matches"]] == ["o::a"]


def test_eval_reports_per_level_hit_rates(client, dsn, fleet):
    ids = seed(dsn)
    for i, (fn, leaf) in enumerate([(synth.peak(), "단일 피크"), (synth.peak(), "단일 피크"),
                                    (synth.edge_drop(), "가장자리 저하"),
                                    (synth.edge_drop(), "가장자리 저하")]):
        name = f"e{i}.tdf"
        fleet.put(1, name, file_of(synth.scan(fn, gain=1.0 + i, source=f"{name}::a")))
        label(client, 1, name, f"{name}::a", ids[leaf])
    store = Store(dsn)
    try:
        res = run_eval(store, set_name="dump", same_scan_only=True)
    finally:
        store.close()
    assert res["labelled"] == 4
    assert res["levels"][2]["n"] == 4 and res["levels"][2]["top1"] == 4
    assert res["levels"][3]["top1"] == 4


def test_the_guide_is_served_with_the_numbers_the_code_uses(client):
    # 읽는 법은 판단을 내린 쪽이 내주고, 숫자는 지금 상수로 채운다 — 문서가 코드와 갈라지지 않게 (SL-DOC-1)
    r = client.get("/guide")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "{{" not in r.text and "}}" not in r.text
    assert f"{shape.GRID_N}×{shape.GRID_N}" in r.text and f"{shape.CLUSTER_MIN} 이상" in r.text
    for anchor in ("skew", "pca", "path", "detail"):          # 화면이 절로 바로 건다 (poc_viewer DUMP-CLU-18)
        assert f'id="{anchor}"' in r.text
