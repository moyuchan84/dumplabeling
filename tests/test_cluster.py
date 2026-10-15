"""기록 풀 · 묶음 · 유사 사례 — HTTP 왕복 (SPEC.md 「기록 풀」·「묶음」·「유사 사례」)."""
from __future__ import annotations

import httpx

from scanlabel.fleet_client import Fleet
from scanlabel.jobs import index
from scanlabel.store import Store

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


def open_file(client, server_id, name):
    r = client.post("/labels/similar", json={"server_id": server_id, "name": name})
    assert r.status_code == 200, r.text


def test_opening_a_file_and_labelling_put_records_in_the_pool(client, dsn, fleet):
    ids = seed(dsn)
    fleet.put(1, "a.tdf", file_of(synth.scan(synth.peak(), source="a::1"), synth.scan(synth.ramp(), source="a::2")))
    fleet.put(1, "b.tdf", file_of(synth.scan(synth.peak(), source="b::1")))
    assert client.get("/clusters").json() == {"run": None, "pool": 0, "clusters": []}
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

    r = client.post("/clusters/run", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["run"]["clusters"] == 2 and res["run"]["unclustered"] == 1 and res["run"]["stale"] is False
    first, second = res["clusters"]
    assert first["size"] == 4 and first["labelled"] == 2 and first["unlabelled"] == 2
    assert first["category"]["path"] == "신호 이상 › 피크 › 단일 피크"
    assert first["category"]["case_id"] == 1 and first["category"]["share"] == 1.0
    assert len(first["path"]) == 128 and first["channel"] == "A"
    assert second["size"] == 3 and second["category"] is None    # 아무도 라벨하지 않았다

    # 묶음 상세 — 대표 먼저, 구성원마다 라벨과 겹쳐 그릴 경로
    detail = client.get(f"/clusters/{second['cluster_no']}", params={"run_id": res["run"]["id"]}).json()
    assert detail["members"][0]["representative"] is True
    assert {m["name"] for m in detail["members"]} == set(edges)
    assert all(len(m["path"]) == 128 for m in detail["members"])

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
    run = client.post("/clusters/run", json={}).json()
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
    client.post("/clusters/run", json={})
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
        put_files(fleet, 1, "j", synth.peak(), 1)
        assert index(store, client, set_name="dump")["files"] == 1
        assert store.pool_count("dump") == 4
    finally:
        client.close()
        store.close()
