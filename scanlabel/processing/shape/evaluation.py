"""묶음의 분류 · 품질 · leave-one-out 평가."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from ..tree import MAX_LEVEL, Tree
from .ranking import rank
from .constants import CLUSTER_AGREE, LEVEL_TOP


def cluster_category(member_labels: list[list[int]], tree: Tree) -> dict:
    """한 묶음의 분류 — 라벨된 구성원들의 라벨을 단계별로 센다 (SL-CLU-4).

    `member_labels` = 구성원마다 붙은 라벨 id(라벨 없으면 빈 목록). 라벨은 조상까지 센다(SL-SUG-1).
    단계 L 의 노드 몫 = 그 노드를 가진 라벨된 구성원 / 라벨된 구성원 전체. 단계 L 이 **합의**되려면
    1위 몫이 `CLUSTER_AGREE` 이상이고, 2위보다 많고, (L>1 이면) 바로 위 단계의 합의 노드의 자식이어야
    한다. `category` = 합의된 가장 깊은 단계(없으면 None) — 라벨 안 된 구성원에게 붙일 후보다.
    """
    labelled = [[int(i) for i in ids if int(i) in tree] for ids in member_labels]
    labelled = [ids for ids in labelled if ids]
    n = len(labelled)
    counts: dict[int, int] = {}
    for ids in labelled:
        for node in tree.closure(ids):
            counts[node] = counts.get(node, 0) + 1
    levels: dict[int, list[dict]] = {}
    chosen: Optional[int] = None
    category = None
    for level in range(1, MAX_LEVEL + 1):
        row = sorted(((i, c) for i, c in counts.items() if int(tree.get(i)["level"]) == level),
                     key=lambda t: (-t[1], t[0]))
        agreed = False
        if row and n and (level == 1 or chosen is not None):
            top_id, top = row[0]
            parent = tree.get(top_id)["parent_id"]
            agreed = (top / n >= CLUSTER_AGREE
                      and (len(row) < 2 or top > row[1][1])
                      and (level == 1 or (parent is not None and int(parent) == chosen)))
        chosen = row[0][0] if agreed else None
        levels[level] = [
            {"label_id": i, "path": tree.path(i), "count": c, "share": c / n,
             "case_id": tree.case_for(i), "agreed": agreed and j == 0}
            for j, (i, c) in enumerate(row[:LEVEL_TOP])
        ]
        if agreed:
            category = {**levels[level][0], "level": level}
    return {"labelled": n, "unlabelled": len(member_labels) - n, "levels": levels, "category": category}


def cluster_quality(result: Mapping, labels: Mapping[Any, list[int]], tree: Tree) -> dict:
    """묶음 결과를 라벨로 잰다 — 두 잣대(SL-CLU-10)를 실데이터로 견주는 숫자.

    `labels` = 기록 id → 붙은 라벨 id. 단계 L 의 **순도** = 묶음마다 (가장 많은 노드를 가진 구성원 수)의
    합 / (그 단계까지 라벨된 묶인 구성원 수). **덮음** = 라벨된 기록 중 어느 묶음에든 든 몫. 묶음 수가
    많고 작을수록 순도는 쉽게 오른다 — 둘을 함께 본다.
    """
    ids = [m["id"] for c in result["clusters"] for m in c["members"]] + list(result["unclustered"])
    labelled = {i for i in ids if any(int(x) in tree for x in labels.get(i) or [])}
    clustered = {m["id"] for c in result["clusters"] for m in c["members"]} & labelled
    purity: dict[str, Optional[float]] = {}
    for level in range(1, MAX_LEVEL + 1):
        hit = tot = 0
        for c in result["clusters"]:
            sets = []
            for m in c["members"]:
                if m["id"] in labelled:
                    nodes = {x for x in tree.closure(int(i) for i in labels[m["id"]] if int(i) in tree)
                             if int(tree.get(x)["level"]) == level}
                    if nodes:
                        sets.append(nodes)
            if not sets:
                continue
            counts: dict[int, int] = {}
            for s in sets:
                for x in s:
                    counts[x] = counts.get(x, 0) + 1
            hit += max(counts.values())
            tot += len(sets)
        purity[str(level)] = round(hit / tot, 3) if tot else None
    return {"labelled": len(labelled), "clustered_labelled": len(clustered),
            "coverage": round(len(clustered) / len(labelled), 3) if labelled else None, "purity": purity}


def partition_agreement(a: Mapping, b: Mapping) -> Optional[float]:
    """두 묶음 결과가 얼마나 같게 갈랐나 — adjusted Rand index (1 같음, 0 우연 수준). 묶이지 않은 기록은 혼자."""
    def lab(r: Mapping) -> dict:
        out = {m["id"]: f"c{n}" for n, c in enumerate(r["clusters"]) for m in c["members"]}
        out.update({i: f"u{i}" for i in r["unclustered"]})
        return out
    la, lb = lab(a), lab(b)
    ids = sorted(la.keys() & lb.keys(), key=str)
    n = len(ids)
    if n < 2:
        return None
    pair = lambda x: x * (x - 1) / 2
    cells: dict[tuple, int] = {}
    ra: dict[str, int] = {}
    rb: dict[str, int] = {}
    for i in ids:
        cells[(la[i], lb[i])] = cells.get((la[i], lb[i]), 0) + 1
        ra[la[i]] = ra.get(la[i], 0) + 1
        rb[lb[i]] = rb.get(lb[i], 0) + 1
    index = sum(pair(v) for v in cells.values())
    sa, sb = sum(pair(v) for v in ra.values()), sum(pair(v) for v in rb.values())
    expected = sa * sb / pair(n)
    top = (sa + sb) / 2
    return 1.0 if top == expected else round((index - expected) / (top - expected), 3)


def evaluate(labelled: list[Mapping], tree: Tree, *, same_scan_only: bool = True) -> dict:
    """leave-one-out — 라벨 하나를 빼고 나머지로 맞히는 단계별 top-1 / top-3 적중률.

    정답 = 그 기록의 라벨 closure 중 그 단계의 노드들. 그 단계까지 라벨이 내려가지 않은 기록은
    그 단계의 분모에서 빠진다(중분류에서 멈춘 라벨은 소분류 적중률에 들어가지 않는다).
    """
    out = {level: {"n": 0, "top1": 0, "top3": 0, "suggested": 0, "suggested_right": 0}
           for level in range(1, MAX_LEVEL + 1)}
    for i, item in enumerate(labelled):
        others = [c for j, c in enumerate(labelled) if j != i
                  and (not same_scan_only or c.get("scan_enum", "") == item.get("scan_enum", ""))]
        truth = tree.closure(x for x in item["label_ids"] if int(x) in tree)
        res = rank(item["features"], others, tree, k=0)
        for level in range(1, MAX_LEVEL + 1):
            want = {t for t in truth if int(tree.get(t)["level"]) == level}
            if not want:
                continue
            row = res["levels"].get(level, [])
            o = out[level]
            o["n"] += 1
            if row and row[0]["label_id"] in want:
                o["top1"] += 1
            if any(e["label_id"] in want for e in row[:3]):
                o["top3"] += 1
            if row and row[0]["suggested"]:
                o["suggested"] += 1
                if row[0]["label_id"] in want:
                    o["suggested_right"] += 1
    return out
