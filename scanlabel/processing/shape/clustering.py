"""묶음(클러스터) — 계층 묶기 · 무리 수 고르기 · 지난 판과 비교."""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from .constants import CLUSTER_K_MAX, CLUSTER_MIN_SIZE, METHODS
from .skew import prepare_skew, skew_baseline, skew_similarity
from .pca import pca_coords, pca_model, pca_similarity


def _txt(v: Any) -> str:
    return "" if v is None else str(v)


def group_of(it: Mapping) -> tuple[str, str, str]:
    """묶음 범위 — (스캔 종류, 척, 센서). 없는 값은 ''."""
    return (_txt(it.get("scan_enum")), _txt(it.get("chuck_id")), _txt(it.get("sensor_id")))


def group_key(scan_enum: Any, chuck_id: Any, sensor_id: Any) -> str:
    """묶음 범위 하나의 이름 — (스캔 종류, 척, 센서). 지도의 투영식도 이 키로 남는다 (SL-CLU-3 · SL-MAP-1)."""
    return f"{_txt(scan_enum)}|{_txt(chuck_id)}|{_txt(sensor_id)}"


def _scorer(group: list[Mapping], method: str, model: Optional[Mapping]):
    """묶음 범위 하나의 쌍 점수 — `skew`: 채널 간 편차(기준선을 빼고), `pca`: 주성분 좌표."""
    if method == "pca":
        coords = [pca_coords(model, g["features"]) if model else None for g in group]
        return lambda i, j: pca_similarity(coords[i], coords[j])
    base = skew_baseline(g["features"] for g in group)
    prep = [prepare_skew(g["features"], base) for g in group]

    def score(i: int, j: int) -> Optional[float]:
        sim = skew_similarity(prep[i], prep[j])
        return sim["score"] if sim else None
    return score


def check_method(method: str) -> None:
    if method not in METHODS:
        raise ValueError(f"묶음 잣대는 {', '.join(METHODS)} 중 하나: {method!r}")


def _linkage(d: list[list[float]]) -> list[tuple[float, int, int]]:
    """평균 연결 계층 묶기 — 합친 순서 `[(높이, 남는 자리, 사라지는 자리)…]` (SL-CLU-2).

    NN-chain: 서로가 가장 가까운 두 무리를 합치고, 합친 무리와 나머지의 거리는 구성원 쌍 거리의 평균
    (Lance–Williams). 평균 연결은 합친 높이가 줄지 않아(reducible) 높이 순으로 늘어놓으면 덴드로그램이 된다.
    거리 행렬 한 장 위에서 O(n²). 동점은 낮은 번호 → 결정적이다.
    """
    n = len(d)
    w = [row[:] for row in d]
    size = [1] * n
    alive = [True] * n
    merges: list[tuple[float, int, int]] = []
    chain: list[int] = []
    left = n
    while left > 1:
        if not chain:
            chain.append(next(i for i in range(n) if alive[i]))
        a = chain[-1]
        prev = chain[-2] if len(chain) > 1 else -1
        row = w[a]
        best, bd = prev, (row[prev] if prev >= 0 else math.inf)
        for c in range(n):
            if alive[c] and c != a and row[c] < bd:
                best, bd = c, row[c]
        if best != prev:
            chain.append(best)
            continue
        chain.pop()
        chain.pop()
        lo, hi = min(a, best), max(a, best)
        merges.append((bd, lo, hi))
        na, nb = size[lo], size[hi]
        rl, rh = w[lo], w[hi]
        for c in range(n):
            if alive[c] and c != lo and c != hi:
                v = (na * rl[c] + nb * rh[c]) / (na + nb)
                rl[c] = v
                w[c][lo] = v
        alive[hi] = False
        size[lo] = na + nb
        left -= 1
    return sorted(merges, key=lambda m: m[0])         # 안정 정렬 — 같은 높이는 합친 순서대로


def _cut(n: int, merges: list[tuple[float, int, int]], k: int) -> list[int]:
    """덴드로그램을 무리 k 개로 자른다 — 기록마다 무리의 대표 자리(가장 작은 번호)."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for _, a, b in merges[:n - k]:
        ra, rb = find(a), find(b)
        parent[max(ra, rb)] = min(ra, rb)
    return [find(i) for i in range(n)]


def _silhouette(d: list[list[float]], lab: list[int]) -> tuple[float, int]:
    """평균 실루엣 — 기록마다 (가장 가까운 남의 무리까지 평균 거리 − 제 무리 평균 거리) / 둘 중 큰 것.

    1 = 제 무리와는 붙고 남과는 멀다, 0 = 경계에 있다. **`CLUSTER_MIN_SIZE` 보다 작은 무리는 잡음**이다: 잡음 기록은 0 으로
    평균에 들고(묶음을 잡음으로 흩으면 손해), 「가장 가까운 남의 무리」로는 세지 않는다 — 세면 동떨어진 기록 하나를 떼어
    내는 갈림이 가장 좋아 보여 진짜 모양 갈래가 갈리지 않는다(2026-11-02, 실제 풀의 한 범위가 「303건 + 1건」으로
    잘렸다). 반환 = (실루엣, 묶음이 될 무리 수). 묶음이 될 무리가 둘 미만이면 실루엣 0.
    """
    groups: dict[int, list[int]] = {}
    for i, g in enumerate(lab):
        groups.setdefault(g, []).append(i)
    real = [(g, ms) for g, ms in groups.items() if len(ms) >= CLUSTER_MIN_SIZE]
    if len(real) < 2:
        return 0.0, len(real)
    total = 0.0
    for g, ms in real:
        for i in ms:
            get = d[i].__getitem__
            a = sum(map(get, ms)) / (len(ms) - 1)
            b = min(sum(map(get, other)) / len(other) for h, other in real if h != g)
            m = max(a, b)
            total += (b - a) / m if m > 0 else 0.0
    return total / len(lab), len(real)


def best_cut(d: list[list[float]]) -> tuple[list[int], int, float]:
    """문턱 없이 자를 자리를 고른다 — 묶음이 될 무리(`CLUSTER_MIN_SIZE` 이상)가 2 … `CLUSTER_K_MAX` 개인 갈림 중
    평균 실루엣이 가장 큰 것(동점은 적은 쪽).

    「대표와 0.8 이상」 같은 닮음 문턱이 없다: 서로 가까운 것끼리 먼저 합치고, 무리 안은 붙고 무리 사이는 먼
    자리에서 자른다(SL-CLU-2). 덴드로그램을 무리 2 … `4 × CLUSTER_K_MAX` 개로 잘라 보며, 작은 무리(잡음)는 세지도
    재지도 않는다. 가장 나은 갈림도 실루엣이 0 이하면 가를 것이 없다는 뜻 — 한 무리(k = 1).
    반환 = (기록마다 무리, 묶음이 될 무리 수, 그 실루엣).
    """
    n = len(d)
    merges = _linkage(d)
    best: Optional[tuple[float, int, list[int]]] = None
    for k in range(2, min(4 * CLUSTER_K_MAX, n - 1) + 1):
        lab = _cut(n, merges, k)
        s, real = _silhouette(d, lab)
        if real > CLUSTER_K_MAX:
            break
        if real >= 2 and (best is None or s > best[0] + 1e-12):
            best = (s, real, lab)
    # 어떤 갈림도 기록을 남보다 제 무리에 더 가깝게 두지 못하면(실루엣 ≤ 0 — 다 같은 모양) 범위 전체가 한 묶음.
    if best is None or best[0] <= 1e-9:
        return [0] * n, 1, 0.0
    return best[2], best[1], best[0]


def cluster(items: list[Mapping], method: str = "skew", *, models: Optional[Mapping] = None,
            previous: Optional[Mapping] = None) -> dict:
    """라벨과 무관하게 본 기록들을 모양으로 묶는다 (SL-CLU-2).

    items 의 각 항목: `id` · `features` · `scan_enum` · `chuck_id` · `sensor_id`(없으면 ''). 호출자가 범위마다
    `CLUSTER_MAX_N` 개로 잘라서 넣는다(라벨된 기록은 자르지 않는다, SL-CLU-12). **(스캔 종류, 척, 센서)** 마다
    따로 묶는다 — 경로가 다르면 1D 비교가 무의미하고(SL-SIM-2), 척 · 센서가 다르면 신호의 기준이 달라 모양의
    차이가 원인의 차이가 아니다(SL-CLU-3).

    잣대는 `method` — **채널 간 편차**(`skew`, SL-CLU-9) 또는 **주성분**(`pca`, SL-CLU-10; `models` 는
    범위 → `pca_model`, 없으면 여기서 낸다). 누구와도 비교할 수 없는 기록은 「묶이지 않음」이다.

    방법(문턱 없는 계층 묶기, 2026-11-02): 범위 안의 모든 쌍을 비교해 거리(1 − 점수, 비교할 수 없는 쌍은 1)를
    내고, **평균 연결**로 가까운 무리부터 합친 뒤, 무리 수 2 … `CLUSTER_K_MAX` 중 **평균 실루엣**이 가장 큰
    자리에서 자른다(`best_cut`). `CLUSTER_MIN_SIZE` 보다 작은 무리는 「묶이지 않음」이다. 대표 = 무리 안에서
    나머지와의 거리 합이 가장 작은 기록(medoid), 구성원의 `score` = 대표와의 점수. 같은 풀이면 언제나 같은 결과다.

    `previous`(지난 판 — `{"clusters": [{"no", "representative", "members": [id…]}…], "next_no"}`)를 주면
    **번호를 잇는다**(SL-CLU-13): 이번 묶음마다 지난 묶음과 겹치는 기록 수를 세어, 가장 많이 겹치는 짝부터
    지난 번호를 물려준다(한 번호는 한 묶음에만). 나머지는 쓴 적 없는 번호를 받는다. 갈림은 풀만으로 정해지므로
    아무것도 바뀌지 않았으면 다시 눌러도 같은 갈림 · 같은 번호다. `changes` = 지난 판과 견준 기록 수.
    """
    check_method(method)
    groups: dict[tuple[str, str, str], list[Mapping]] = {}
    for it in items:
        groups.setdefault(group_of(it), []).append(it)

    clusters: list[dict] = []
    unclustered: list = []
    for scan_enum, chuck_id, sensor_id in sorted(groups):
        group = groups[(scan_enum, chuck_id, sensor_id)]
        ids = [g["id"] for g in group]
        model = None
        if method == "pca":
            gid = (scan_enum, chuck_id, sensor_id)
            model = models[gid] if models is not None and gid in models else pca_model(g["features"] for g in group)
        score = _scorer(group, method, model)
        n = len(group)
        sims: list[list[Optional[float]]] = [[None] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sims[i][j] = sims[j][i] = score(i, j)
        ok = [i for i in range(n) if any(sims[i][j] is not None for j in range(n) if j != i)]
        okset = set(ok)
        unclustered += [ids[i] for i in range(n) if i not in okset]
        if len(ok) < CLUSTER_MIN_SIZE:
            unclustered += [ids[i] for i in ok]
            continue
        dist = [[0.0 if i == j else (1.0 - sims[i][j] if sims[i][j] is not None else 1.0) for j in ok] for i in ok]
        lab, _, _ = best_cut(dist)
        parts: dict[int, list[int]] = {}
        for p, g in enumerate(lab):
            parts.setdefault(g, []).append(p)
        for ps in parts.values():
            if len(ps) < CLUSTER_MIN_SIZE:
                unclustered += [ids[ok[p]] for p in ps]
                continue
            rep_p = min(ps, key=lambda p: (sum(dist[p][q] for q in ps), _order_key(ids[ok[p]])))
            rep = ok[rep_p]
            members = [{"id": ids[rep], "score": 1.0, "representative": True}]
            members += sorted(({"id": ids[ok[p]], "score": sims[rep][ok[p]] if sims[rep][ok[p]] is not None else 0.0,
                                "representative": False} for p in ps if p != rep_p),
                              key=lambda m: (-m["score"], _order_key(m["id"])))
            clusters.append({"scan_enum": scan_enum, "chuck_id": chuck_id, "sensor_id": sensor_id,
                             "representative": ids[rep], "members": members, "no": None})

    clusters.sort(key=lambda c: (-len(c["members"]), c["scan_enum"], c["chuck_id"], c["sensor_id"],
                                 _order_key(c["representative"])))
    # 번호 — 지난 묶음과 가장 많이 겹치는 짝부터 지난 번호를, 나머지는 쓴 적 없는 번호(SL-CLU-13).
    prev = list((previous or {}).get("clusters") or [])
    prev_by_rec = {m: int(p["no"]) for p in prev for m in p["members"]}
    pairs = []
    for k, c in enumerate(clusters):
        hits: dict[int, int] = {}
        for m in c["members"]:
            no = prev_by_rec.get(m["id"])
            if no is not None:
                hits[no] = hits.get(no, 0) + 1
        pairs += [(-cnt, no, k) for no, cnt in hits.items()]
    used_no: set[int] = set()
    for _, no, k in sorted(pairs):
        if clusters[k]["no"] is None and no not in used_no:
            clusters[k]["no"] = no
            used_no.add(no)
    next_no = max([int((previous or {}).get("next_no") or 1)] + [int(p["no"]) + 1 for p in prev])
    for c in clusters:
        if c["no"] is None:
            c["no"] = next_no
            next_no += 1
    out = {"clusters": clusters, "unclustered": sorted(unclustered, key=_order_key), "next_no": next_no}
    out["changes"] = run_changes(previous, out) if previous is not None else None
    return out


def run_changes(previous: Optional[Mapping], result: Mapping) -> dict:
    """지난 판과 견준 이번 판 — 기록 수로 (SL-CLU-13). 화면이 「지난 판과 무엇이 달라졌나」를 말한다.

    `kept` 같은 번호에 남음 · `moved` 다른 번호로 옮김 · `joined` 지난 판에 묶음이 없던 기록이 묶임(새 기록 포함) ·
    `released` 묶음에 있다가 이번엔 묶이지 않음 · `out` 지난 판 구성원이 이번 판에 없음(풀에서 빠짐) ·
    `new_clusters` 새 번호 · `closed_clusters` 이번 판에 없는 지난 번호.
    """
    before = {m: int(p["no"]) for p in (previous or {}).get("clusters") or [] for m in p["members"]}
    after = {m["id"]: int(c["no"]) for c in result["clusters"] for m in c["members"]}
    seen = set(after) | set(result["unclustered"])
    old_nos = {int(p["no"]) for p in (previous or {}).get("clusters") or []}
    new_nos = {int(c["no"]) for c in result["clusters"]}
    return {
        "kept": sum(1 for r, no in after.items() if before.get(r) == no),
        "moved": sum(1 for r, no in after.items() if r in before and before[r] != no),
        "joined": sum(1 for r in after if r not in before),
        "released": sum(1 for r in result["unclustered"] if r in before),
        "out": sum(1 for r in before if r not in seen),
        "new_clusters": len(new_nos - old_nos),
        "closed_clusters": len(old_nos - new_nos),
    }


def _order_key(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0
