"""라벨 트리의 순수 판단 — DB 도 HTTP 도 모른다 (SPEC.md 「라벨 트리」).

트리는 `label_defs` 행의 평평한 목록(`{id, parent_id, level, name, retired, …}`)으로 들어오고,
여기서 경로 · 조상 · 실효 내림 상태 · 선택 검증을 계산한다. Store 와 shape 가 같은 규칙을 쓴다.
"""
from __future__ import annotations

from typing import Iterable, Mapping

MAX_LEVEL = 3
LEVEL_NAMES = {1: "대분류", 2: "중분류", 3: "소분류"}
PATH_SEP = " › "


class Tree:
    """id → 행. 행은 최소 `id`·`parent_id`·`level`·`name`·`retired` 를 가진다."""

    def __init__(self, rows: Iterable[Mapping]):
        self.rows: dict[int, Mapping] = {int(r["id"]): r for r in rows}

    def __contains__(self, node_id: int) -> bool:
        return node_id in self.rows

    def get(self, node_id: int) -> Mapping:
        return self.rows[node_id]

    def ancestors(self, node_id: int) -> list[int]:
        """자기 자신부터 대분류까지 (자신 포함, 깊은 것 먼저)."""
        out: list[int] = []
        cur = node_id
        while cur is not None and cur in self.rows:
            out.append(cur)
            if len(out) > MAX_LEVEL:      # 손상된 데이터의 순환을 막는다 — 트리는 3단계다
                break
            parent = self.rows[cur]["parent_id"]
            cur = int(parent) if parent is not None else None
        return out

    def path(self, node_id: int) -> str:
        return PATH_SEP.join(str(self.rows[i]["name"]) for i in reversed(self.ancestors(node_id)))

    def retired(self, node_id: int) -> bool:
        """실효 내림 — 자기나 조상 중 하나라도 내려졌으면 고를 수 없다."""
        return any(bool(self.rows[i]["retired"]) for i in self.ancestors(node_id))

    def children(self, node_id: int | None) -> list[int]:
        return [i for i, r in self.rows.items()
                if (int(r["parent_id"]) if r["parent_id"] is not None else None) == node_id]

    def closure(self, ids: Iterable[int]) -> set[int]:
        """붙은 라벨들과 그 조상 전부 — 소분류 라벨은 중 · 대분류에도 속한다."""
        out: set[int] = set()
        for i in ids:
            out.update(self.ancestors(int(i)))
        return out

    def case_for(self, node_id: int):
        """가장 깊은 단계의 연결 사례, 없으면 위 단계의 것 (SPEC.md 「사례 연결」)."""
        for i in self.ancestors(node_id):
            cid = self.rows[i].get("case_id")
            if cid is not None:
                return cid
        return None


def check_selection(tree: Tree, ids: Iterable[int]) -> list[int]:
    """기록에 붙일 라벨 id 를 검증하고 정리한다(중복 제거 · 얕은 단계 먼저). 어기면 ValueError.

    - 없는 id, 내려진 노드(조상이 내려진 것 포함)는 붙일 수 없다.
    - 조상과 자손을 함께 붙이지 않는다 — 자손이 조상을 이미 함의한다.
    """
    picked: list[int] = []
    for raw in ids:
        i = int(raw)
        if i not in tree:
            raise ValueError(f"없는 라벨 id: {i}")
        if tree.retired(i):
            raise ValueError(f"내려진 라벨은 붙일 수 없다: {tree.path(i)}")
        if i not in picked:
            picked.append(i)
    for i in picked:
        for anc in tree.ancestors(i)[1:]:
            if anc in picked:
                raise ValueError(
                    f"조상과 자손을 함께 붙일 수 없다: {tree.path(anc)} 와 {tree.path(i)} "
                    f"— 더 깊은 쪽 하나만 붙인다")
    return sorted(picked, key=lambda i: (int(tree.get(i)["level"]), i))


def child_level(tree: Tree, parent_id: int | None) -> int:
    """새 노드의 단계 — 부모에서 정해진다. 소분류 아래에는 만들 수 없다."""
    if parent_id is None:
        return 1
    if parent_id not in tree:
        raise ValueError(f"없는 부모 id: {parent_id}")
    level = int(tree.get(parent_id)["level"]) + 1
    if level > MAX_LEVEL:
        raise ValueError("소분류 아래에는 라벨을 만들 수 없다 — 트리는 대 › 중 › 소 3단계다")
    if tree.retired(parent_id):
        raise ValueError(f"내려진 라벨 아래에는 만들 수 없다: {tree.path(parent_id)}")
    return level


def check_move(tree: Tree, node_id: int, new_parent: int | None) -> None:
    """같은 단계의 부모로만 옮길 수 있다 (자식은 따라간다)."""
    node = tree.get(node_id)
    old_parent = node["parent_id"]
    if new_parent is None or old_parent is None:
        if (new_parent is None) != (old_parent is None):
            raise ValueError("대분류는 부모를 가질 수 없고, 중 · 소분류는 부모 없이 둘 수 없다")
        return
    if new_parent not in tree:
        raise ValueError(f"없는 부모 id: {new_parent}")
    if int(tree.get(new_parent)["level"]) != int(tree.get(int(old_parent))["level"]):
        raise ValueError(
            f"같은 단계의 부모로만 옮길 수 있다 — {LEVEL_NAMES[int(node['level'])]} 의 부모는 "
            f"{LEVEL_NAMES[int(node['level']) - 1]} 다")
