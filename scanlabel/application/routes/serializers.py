"""응답 모양 만들기 — 반올림 · 라벨 경로 · 묶음 분류. 판단은 하지 않고 옮겨 적기만 한다."""
from __future__ import annotations

from typing import Optional


def _r(x: Optional[float], digits: int = 3) -> Optional[float]:
    return None if x is None else round(x, digits)


def _sim_out(sim: dict) -> dict:
    return {
        "score": _r(sim["score"]), "d1": _r(sim["d1"]), "d2": _r(sim["d2"]),
        "scale": _r(sim["scale"]),
        "by_channel": {k: {"score": _r(v["score"]), "d1": _r(v["d1"]), "d2": _r(v["d2"]),
                           "dx": _r(v["dx"]), "dz": _r(v["dz"]), "scale": _r(v["scale"])}
                       for k, v in sim["by_channel"].items()},
    }


def _skew_out(sim: Optional[dict]) -> Optional[dict]:
    """채널 간 편차 점수 — 묶음 · 지도가 쓰는 잣대 (SL-CLU-9)."""
    if sim is None:
        return None
    return {"score": _r(sim["score"]), "d1": _r(sim["d1"]), "d2": _r(sim["d2"]),
            "dx": _r(sim["dx"]), "dz": _r(sim["dz"]), "dp": _r(sim.get("dp")), "channels": sim["channels"]}


def _level_out(entries: list[dict]) -> list[dict]:
    return [{**e, "score": _r(e["score"])} for e in entries]


def _labels_out(tree, ids) -> list[dict]:
    return [{"id": int(i), "level": int(tree.get(int(i))["level"]), "path": tree.path(int(i)),
             "case_id": tree.case_for(int(i))} for i in ids or [] if int(i) in tree]


def _record_ref(r: dict) -> dict:
    return {"record_id": r["record_id"], "servername": r["servername"], "server_id": r["server_id"],
            "name": r["name"], "source": r["source"], "sig": r["sig"], "scan_enum": r["scan_enum"],
            "chuck_id": r.get("chuck_id") or "", "sensor_id": r.get("sensor_id") or "", "meta": r["meta"]}


def _category_out(cat: dict) -> dict:
    def entry(e: dict) -> dict:
        return {**e, "share": _r(e["share"])}
    return {"labelled": cat["labelled"], "unlabelled": cat["unlabelled"],
            "levels": {str(k): [entry(e) for e in v] for k, v in cat["levels"].items()},
            "category": None if cat["category"] is None else entry(cat["category"])}
