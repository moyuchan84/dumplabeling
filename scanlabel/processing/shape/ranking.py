"""순위 · 단계별 제안 · 유사 사례."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from ..tree import MAX_LEVEL, Tree
from .constants import CASE_TOP, EVIDENCE_MIN, LEVEL_TOP, MARGIN, SUGGEST_MIN
from .similarity import prepare, similarity


def rank(target: Mapping, candidates: Iterable[Mapping], tree: Tree, *, k: int = 10) -> dict:
    """라벨된 기록들을 target 과 비교해 상위 k 건과 단계별 제안을 낸다.

    candidates 의 각 항목은 `features` · `label_ids` 와 호출자가 돌려받고 싶은 필드를 가진다.
    단계별 집계(SPEC.md 「제안」): 라벨된 기록은 자기 노드와 그 조상 전부에 표를 던진다.
    노드 점수 = 그 아래 기록의 최고 점수. 단계 L 은 (1) 그 단계 1위가 `SUGGEST_MIN` 이상이고
    (2) 2위보다 `MARGIN` 이상 앞서고 (3) 단계 L-1 이 확실하며 1위가 그 자식일 때만 확실하다.
    """
    scored = []
    target = prepare(target)
    for c in candidates:
        sim = similarity(target, c["features"])
        if sim is None or sim["score"] is None:
            continue
        scored.append((sim, c))
    scored.sort(key=lambda t: -t[0]["score"])

    nodes: dict[int, dict] = {}
    for sim, c in scored:
        for node_id in tree.closure(i for i in c["label_ids"] if int(i) in tree):
            n = nodes.setdefault(node_id, {"score": -1.0, "count": 0})
            n["score"] = max(n["score"], sim["score"])
            if sim["score"] >= EVIDENCE_MIN:
                n["count"] += 1

    levels: dict[int, list[dict]] = {}
    chosen: Optional[int] = None
    for level in range(1, MAX_LEVEL + 1):
        row = sorted(((i, n) for i, n in nodes.items() if int(tree.get(i)["level"]) == level),
                     key=lambda t: (-t[1]["score"], t[0]))
        sure = False
        if row and (level == 1 or chosen is not None):
            top_id, top = row[0]
            second = row[1][1]["score"] if len(row) > 1 else -1.0
            parent = tree.get(top_id)["parent_id"]
            sure = (top["score"] >= SUGGEST_MIN
                    and top["score"] - second >= MARGIN
                    and (level == 1 or (parent is not None and int(parent) == chosen)))
        chosen = row[0][0] if sure else None
        levels[level] = [
            {"label_id": i, "path": tree.path(i), "score": n["score"], "count": n["count"],
             "case_id": tree.case_for(i), "suggested": sure and j == 0}
            for j, (i, n) in enumerate(row[:LEVEL_TOP])
        ]
        if not sure:
            chosen = None

    deepest = None
    for level in range(MAX_LEVEL, 0, -1):
        hit = next((e for e in levels.get(level, []) if e["suggested"]), None)
        if hit:
            deepest = {**hit, "level": level}
            break

    return {
        "matches": [{"similarity": sim, "candidate": c} for sim, c in scored[:k]],
        "levels": levels,
        "suggestion": deepest,
        "cases": similar_cases(scored, tree),
    }


def similar_cases(scored: list[tuple[dict, Mapping]], tree: Tree) -> list[dict]:
    """유사 사례 — 비슷한 기록들의 라벨이 가리키는 사례(acs_case)를 사례 단위로 모은다 (SL-CASE-1).

    한 기록은 붙은 라벨마다 `tree.case_for` 의 사례 하나에 표를 던진다. 사례 점수 = 그 사례로 온
    기록의 최고 점수, `count` = 그중 `EVIDENCE_MIN` 이상인 기록 수. `EVIDENCE_MIN` 아래의 기록만
    가리키는 사례는 싣지 않는다 — 닮지 않은 기록의 사례는 근거가 아니다.
    """
    cases: dict[Any, dict] = {}
    for sim, c in scored:
        if sim["score"] < EVIDENCE_MIN:
            continue
        for lid in c["label_ids"]:
            if int(lid) not in tree:
                continue
            cid = tree.case_for(int(lid))
            if cid is None:
                continue
            e = cases.setdefault(cid, {"case_id": cid, "score": -1.0, "count": 0, "paths": [], "_seen": set()})
            e["score"] = max(e["score"], sim["score"])
            key = id(c)
            if key not in e["_seen"]:
                e["_seen"].add(key)
                e["count"] += 1
            path = tree.path(int(lid))
            if path not in e["paths"]:
                e["paths"].append(path)
    out = sorted(cases.values(), key=lambda e: (-e["score"], -e["count"], str(e["case_id"])))
    for e in out:
        del e["_seen"]
    return out[:CASE_TOP]
