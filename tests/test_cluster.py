"""기록 풀 · 묶음 · 유사 사례 — HTTP 왕복 (SPEC.md 「기록 풀」·「묶음」·「유사 사례」)."""
from __future__ import annotations

import time

import httpx

from scanlabel.connectors.fleet import Fleet
from scanlabel.application.usecases import index
from scanlabel.data.repository import Store

from . import synth
from .test_api import file_of, label, seed


def put_files(fleet, server_id, prefix, fn, n, **kw):
    """한 파일에 기록 하나씩 n 개 — 크기 · offset 만 다른 같은 모양."""
    names = []
    for i in range(n):
        name = f"{prefix}{i}.tdf"
        fleet.put(server_id, name, file_of(synth.scan(fn, gain=1.0 + i, offset=float(i), source=f"{name}::a",
                                                      **kw)))
        names.append(name)
    return names


def rerun(client) -> dict:
    """다시 묶기는 뒤에서 돈다(202) — 끝날 때까지 목록을 다시 읽고 그 응답을 돌려준다."""
    r = client.post("/clusters/run", json={})
    assert r.status_code == 202 and r.json()["job"]["state"] in ("running", "done")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        res = client.get("/clusters").json()
        if res["job"] and res["job"]["state"] != "running":
            assert res["job"]["state"] == "done", res["job"]
            return res
        time.sleep(0.05)
    raise AssertionError("다시 묶기가 끝나지 않았다")


def open_file(client, server_id, name):
    r = client.post("/labels/similar", json={"server_id": server_id, "name": name})
    assert r.status_code == 200, r.text


def test_opening_a_file_and_labelling_put_records_in_the_pool(client, dsn, fleet):
    ids = seed(dsn)
    fleet.put(1, "a.tdf", file_of(synth.scan(synth.peak(), source="a::1"), synth.scan(synth.ramp(), source="a::2")))
    fleet.put(1, "b.tdf", file_of(synth.scan(synth.peak(), source="b::1")))
    assert client.get("/clusters").json() == {"run": None, "pool": 0, "clusters": [], "job": None}
    open_file(client, 1, "a.tdf")                               # 파일을 연다 → 두 기록이 풀에
    label(client, 1, "b.tdf", "b::1", ids["단일 피크"])          # 라벨 저장 → 그 기록이 풀에
    assert client.get("/clusters").json()["pool"] == 3


def test_clusters_group_unlabelled_records_and_read_the_labelled_members(client, dsn, fleet):
    ids = seed(dsn)
    peaks = put_files(fleet, 1, "p", synth.peak(), 4)
    edges = put_files(fleet, 2, "e", synth.edge_drop(), 3)
    for n in peaks:
        open_file(client, 1, n)
    for n in edges:
        open_file(client, 2, n)
    fleet.put(1, "odd.tdf", file_of(synth.scan(synth.ramp(), source="odd::a")))
    open_file(client, 1, "odd.tdf")
    label(client, 1, peaks[0], f"{peaks[0]}::a", ids["단일 피크"])
    label(client, 1, peaks[1], f"{peaks[1]}::a", ids["단일 피크"])

    res = rerun(client)
    assert res["run"]["clusters"] == 2 and res["run"]["unclustered"] == 1 and res["run"]["stale"] is False
    first, second = res["clusters"]
    assert first["size"] == 4 and first["labelled"] == 2 and first["unlabelled"] == 2
    assert first["category"]["path"] == "신호 이상 › 피크 › 단일 피크"
    assert first["category"]["case_id"] == 1 and first["category"]["share"] == 1.0
    assert len(first["profile"]["x"]) == len(first["profile"]["z"]) == 16 and first["channel"] == "A"
    assert second["size"] == 3 and second["category"] is None    # 아무도 라벨하지 않았다

    # 묶음 상세 — 대표 먼저, 구성원마다 라벨과 겹쳐 그릴 경로
    detail = client.get(f"/clusters/{second['cluster_no']}", params={"run_id": res["run"]["id"]}).json()
    assert detail["members"][0]["representative"] is True
    assert {m["name"] for m in detail["members"]} == set(edges)
    assert all(m["profile"].keys() == {"x", "z"} for m in detail["members"])

    # 이 기록이 든 묶음은 유사 응답에도 실린다
    sim = client.post("/labels/similar", json={"server_id": 2, "name": edges[0], "source": f"{edges[0]}::a"}).json()
    assert sim["cluster"]["cluster_no"] == second["cluster_no"] and sim["cluster"]["size"] == 3

    # 같은 파일을 다시 여는 것은 바뀐 것이 아니다 — 새 기록을 보면 그때 결과가 낡았다고 말한다
    open_file(client, 1, peaks[0])
    assert client.get("/clusters").json()["run"]["stale"] is False
    fleet.put(1, "late.tdf", file_of(synth.scan(synth.peak(), source="late::a")))
    open_file(client, 1, "late.tdf")
    assert client.get("/clusters").json()["run"]["stale"] is True


def test_a_cluster_label_is_applied_to_its_unlabelled_members_at_once(client, dsn, fleet):
    ids = seed(dsn)
    edges = put_files(fleet, 2, "e", synth.edge_drop(), 3)
    for n in edges:
        open_file(client, 2, n)
    label(client, 2, edges[0], f"{edges[0]}::a", ids["레벨 저하"])
    run = rerun(client)
    no, run_id = run["clusters"][0]["cluster_no"], run["run"]["id"]
    calls_before = len(fleet.calls)

    r = client.post(f"/clusters/{no}/apply", json={"run_id": run_id, "label_ids": [ids["가장자리 저하"]]})
    assert r.json() == {"applied": 2, "skipped": 1}               # 이미 라벨된 하나는 그대로
    assert len(fleet.calls) == calls_before                       # 풀의 스냅샷으로 — fleet 을 부르지 않는다
    got = {lab["name"]: lab["labels"][0]["path"]
           for n in edges for lab in client.get("/labels", params={"server_id": 2, "name": n}).json()["labels"]}
    assert got[edges[0]] == "신호 이상 › 레벨 저하"
    assert got[edges[1]] == got[edges[2]] == "신호 이상 › 레벨 저하 › 가장자리 저하"

    # 조상 + 자손을 함께 주면 하나도 쓰지 않고 422, 다시 묶인 뒤의 옛 run_id 도 422
    bad = client.post(f"/clusters/{no}/apply", json={"run_id": run_id, "label_ids": [ids["피크"], ids["단일 피크"]]})
    assert bad.status_code == 422
    rerun(client)
    old = client.post(f"/clusters/{no}/apply", json={"run_id": run_id, "label_ids": [ids["가장자리 저하"]]})
    assert old.status_code == 422 and "다시" in old.json()["detail"]
    assert client.get("/clusters/99", params={"run_id": run_id + 1}).status_code == 404


def test_similar_cases_and_records_of_a_case(client, dsn, fleet):
    ids = seed(dsn)
    peaks = put_files(fleet, 2, "p", synth.peak(), 2)
    for n in peaks:
        label(client, 2, n, f"{n}::a", ids["단일 피크"])           # 단일 피크 → 사례 1
    fleet.put(2, "e.tdf", file_of(synth.scan(synth.edge_drop(), source="e::a")))
    label(client, 2, "e.tdf", "e::a", ids["가장자리 저하"])        # 가장자리 저하 → 사례 2

    fleet.put(1, "new.tdf", file_of(synth.scan(synth.peak(), cols=60, source="new::a")))
    sim = client.post("/labels/similar", json={"server_id": 1, "name": "new.tdf", "source": "new::a"}).json()
    assert [c["case_id"] for c in sim["cases"]] == [1]            # 닮지 않은 가장자리 저하의 사례는 없다
    assert sim["cases"][0]["count"] == 2

    by_case = client.get("/labels/by-case/1").json()
    assert by_case["total"] == 2 and {r["name"] for r in by_case["records"]} == set(peaks)
    assert client.get("/labels/by-case/2").json()["total"] == 1
    # 사례가 조상(중분류)에 걸려 있어도 그 자손 라벨의 기록이 나온다
    client.put(f"/label-defs/{ids['레벨 저하']}", json={"case_id": 9})
    client.put(f"/label-defs/{ids['가장자리 저하']}", json={"case_id": None})
    assert client.get("/labels/by-case/9").json()["total"] == 1


def test_index_follows_the_generation_cursor_without_ftp(dsn, fleet):
    put_files(fleet, 1, "i", synth.peak(), 3)
    fleet.put(2, "gone.tdf", [])                                  # 세대는 있는데 캐시가 비었다
    fleet.files.pop((2, "gone.tdf"))
    store = Store(dsn)
    client = Fleet("http://fleet.test", transport=httpx.MockTransport(fleet.handler))
    try:
        first = index(store, client, set_name="dump", page=2)
        assert first["files"] == 3 and first["records"] == 3 and first["cursor"] == 4
        assert [s["name"] for s in first["skipped"]] == ["gone.tdf"]
        assert all(b.get("source") == "cache" for m, p, b in fleet.calls if m == "POST")
        again = index(store, client, set_name="dump")
        assert again["files"] == 0                                # 커서 뒤에 새 세대가 없다
        put_files(fleet, 1, "j", synth.peak(), 3)
        # 상한에 닿으면 거기서 멈추고, 다음 실행은 바로 뒤부터 — 같은 파일을 다시 읽지 않는다
        part = index(store, client, set_name="dump", max_files=2)
        assert part["files"] == 2 and part["cursor"] == 6
        rest = index(store, client, set_name="dump", max_files=2)
        assert rest["files"] == 1 and rest["cursor"] == 7
        assert store.pool_count("dump") == 6
    finally:
        client.close()
        store.close()


def test_rerun_returns_at_once_and_reports_its_state(client, dsn, fleet):
    seed(dsn)
    for n in put_files(fleet, 1, "p", synth.peak(), 3):
        open_file(client, 1, n)
    first = client.post("/clusters/run", json={})
    assert first.status_code == 202                               # 끝나기를 기다리지 않는다
    res = rerun(client)                                           # 돌고 있으면 그 상태를, 끝나면 결과를
    assert res["job"]["state"] == "done" and res["run"]["clusters"] == 1


def test_the_map_places_every_record_and_the_one_on_screen(client, dsn, fleet):
    ids = seed(dsn)
    peaks = put_files(fleet, 1, "p", synth.peak(), 4)
    edges = put_files(fleet, 2, "e", synth.edge_drop(), 4)
    for n in peaks:
        open_file(client, 1, n)
    for n in edges:
        open_file(client, 2, n)
    fleet.put(1, "odd.tdf", file_of(synth.scan(synth.ramp(), source="odd::a")))
    open_file(client, 1, "odd.tdf")
    label(client, 1, peaks[0], f"{peaks[0]}::a", ids["단일 피크"])
    rerun(client)

    # 경로 순서: "map" 이 묶음 번호로 읽히지 않는다
    m = client.get("/clusters/map", params={"server_id": 1, "name": peaks[1], "source": f"{peaks[1]}::a"})
    assert m.status_code == 200, m.text
    res = m.json()
    assert res["has_map"] and res["scan_enum"] == "FINEHORVERT_SCAN"
    assert len(res["points"]) == 9                                # 묶이지 않은 기록(odd)도 지도에 있다
    by_id = {p["id"]: p for p in res["points"]}
    assert sum(1 for p in res["points"] if p["c"] is None) == 1
    assert sum(1 for p in res["points"] if p["l"]) == 1
    assert {c["size"] for c in res["clusters"]} == {4}
    t = res["target"]
    assert t["in_run"] is True and t["cluster_no"] is not None and by_id[t["record_id"]]["x"] == t["x"]

    # 결과에 없던 기록은 투영으로 놓인다 — 피크 묶음 곁에
    fleet.put(1, "late.tdf", file_of(synth.scan(synth.peak(), gain=5.0, source="late::a")))
    late = client.get("/clusters/map", params={"server_id": 1, "name": "late.tdf", "source": "late::a"}).json()
    assert late["target"]["in_run"] is False and late["target"]["cluster_no"] is None
    peak_no = t["cluster_no"]
    peak_pts = [p for p in late["points"] if p["c"] == peak_no]
    other = [p for p in late["points"] if p["c"] not in (peak_no, None)]

    def near(ps):
        return min((p["x"] - late["target"]["x"]) ** 2 + (p["y"] - late["target"]["y"]) ** 2 for p in ps)
    assert near(peak_pts) < near(other)

    # 점을 누르면 나오는 상세 — 라벨 · 묶음 · 지금 기록과의 유사도와 두 곡선
    d = client.get(f"/records/{t['record_id']}",
                   params={"vs_server_id": 1, "vs_name": "late.tdf", "vs_source": "late::a"}).json()
    assert d["cluster_no"] == peak_no and d["name"] == peaks[1] and set(d["profiles"]) == {"A", "B"}
    assert d["vs"]["similarity"]["score"] > 0.9 and len(d["vs"]["profiles"]["A"]["grid"]) == 256
    assert client.get("/records/999999").status_code == 404


def test_clusters_and_the_map_are_per_scan_kind_and_sensor(client, dsn, fleet):
    # 같은 스캔 종류 · 같은 모양이어도 센서가 다르면 다른 묶음 · 다른 지도 (SL-CLU-3 · SL-MAP-1)
    s2 = put_files(fleet, 1, "a", synth.peak(), 3)                       # 센서 2 (synth 기본)
    s5 = []
    for i in range(3):
        name = f"b{i}.tdf"
        sc = synth.scan(synth.peak(), gain=1.0 + i, offset=float(i), source=f"{name}::a")
        fleet.put(1, name, [sc, synth.meta(sc["source"], sensor_id=5)])
        s5.append(name)
    for n in s2 + s5:
        open_file(client, 1, n)
    res = rerun(client)
    assert sorted((c["sensor_id"], c["size"]) for c in res["clusters"]) == [("2", 3), ("5", 3)]

    m = client.get("/clusters/map", params={"server_id": 1, "name": s5[0], "source": f"{s5[0]}::a"}).json()
    assert (m["scan_enum"], m["sensor_id"]) == ("FINEHORVERT_SCAN", "5")
    assert (m["target_kind"], m["target_sensor"]) == ("FINEHORVERT_SCAN", "5") and m["has_map"]
    assert len(m["points"]) == 3 and m["target"]["in_run"] is True
    assert sorted((k["sensor_id"], k["n"]) for k in m["kinds"]) == [("2", 3), ("5", 3)]
    other = client.get("/clusters/map", params={"scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH1",
                                                "sensor_id": "2"}).json()
    assert other["sensor_id"] == "2" and len(other["points"]) == 3 and other["target"] is None

    no = m["target"]["cluster_no"]
    assert client.get(f"/clusters/{no}").json()["sensor_id"] == "5"


def test_clusters_and_the_map_are_per_scan_kind_chuck_and_sensor(client, dsn, fleet):
    # 같은 스캔 종류 · 센서 · 모양이어도 척이 다르면 다른 묶음 · 다른 지도 (SL-CLU-3 · SL-MAP-1)
    c1 = put_files(fleet, 1, "k", synth.peak(), 3)                       # 척 CH1 (synth 기본)
    c2 = []
    for i in range(3):
        name = f"m{i}.tdf"
        sc = synth.scan(synth.peak(), gain=1.0 + i, offset=float(i), source=f"{name}::a")
        fleet.put(1, name, [sc, synth.meta(sc["source"], chuck_id="CH2")])
        c2.append(name)
    for n in c1 + c2:
        open_file(client, 1, n)
    res = rerun(client)
    assert sorted((c["chuck_id"], c["size"]) for c in res["clusters"]) == [("CH1", 3), ("CH2", 3)]

    m = client.get("/clusters/map", params={"server_id": 1, "name": c2[0], "source": f"{c2[0]}::a"}).json()
    assert (m["scan_enum"], m["chuck_id"], m["sensor_id"]) == ("FINEHORVERT_SCAN", "CH2", "2")
    assert (m["target_kind"], m["target_chuck"], m["target_sensor"]) == ("FINEHORVERT_SCAN", "CH2", "2")
    assert m["has_map"] and len(m["points"]) == 3 and m["target"]["in_run"] is True
    assert sorted((k["chuck_id"], k["n"]) for k in m["kinds"]) == [("CH1", 3), ("CH2", 3)]
    other = client.get("/clusters/map", params={"scan_enum": "FINEHORVERT_SCAN", "chuck_id": "CH1",
                                                "sensor_id": "2"}).json()
    assert other["chuck_id"] == "CH1" and len(other["points"]) == 3 and other["target"] is None

    no = m["target"]["cluster_no"]
    detail = client.get(f"/clusters/{no}").json()
    assert detail["chuck_id"] == "CH2" and set(detail["skew"]) == {"A", "B"}
    # 기록 상세 — 그림 재료는 기록마다 제 것만으로: 상대를 바꿔도 지금 기록의 곡선은 같다
    rid = m["target"]["record_id"]
    pts = [p["id"] for p in m["points"] if p["id"] != rid]
    vs = {"vs_server_id": 1, "vs_name": c2[0], "vs_source": f"{c2[0]}::a"}
    d1, d2 = (client.get(f"/records/{p}", params=vs).json() for p in pts)
    assert d1["vs"]["profiles"] == d2["vs"]["profiles"] and d1["vs"]["skew"] == d2["vs"]["skew"]
    assert d1["vs"]["skew_similarity"]["channels"] == ["A", "B"] and d1["chuck_id"] == "CH2"
