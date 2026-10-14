"""라벨 트리 규칙 — 순수 판단 (SPEC.md 「라벨 트리」)."""
from __future__ import annotations

import pytest

from scanlabel.tree import Tree, check_move, check_selection, child_level

ROWS = [
    {"id": 1, "parent_id": None, "level": 1, "name": "신호 이상", "retired": False},
    {"id": 11, "parent_id": 1, "level": 2, "name": "피크", "retired": False},
    {"id": 111, "parent_id": 11, "level": 3, "name": "단일 피크", "retired": False},
    {"id": 2, "parent_id": None, "level": 1, "name": "위치 이상", "retired": True},
    {"id": 21, "parent_id": 2, "level": 2, "name": "중심 이탈", "retired": False},
]


def test_path_reads_from_the_top_level():
    assert Tree(ROWS).path(111) == "신호 이상 › 피크 › 단일 피크"


def test_a_retired_ancestor_retires_its_descendants():
    t = Tree(ROWS)
    assert t.retired(21)
    with pytest.raises(ValueError, match="내려진"):
        check_selection(t, [21])


def test_an_ancestor_and_its_descendant_cannot_be_picked_together():
    with pytest.raises(ValueError, match="조상과 자손"):
        check_selection(Tree(ROWS), [11, 111])


def test_selection_drops_repeats():
    assert check_selection(Tree(ROWS), [111, 111]) == [111]


def test_the_parent_sets_the_level_and_nothing_goes_below_a_leaf():
    t = Tree(ROWS)
    assert child_level(t, None) == 1 and child_level(t, 1) == 2 and child_level(t, 11) == 3
    with pytest.raises(ValueError, match="소분류 아래"):
        child_level(t, 111)
    with pytest.raises(ValueError, match="내려진"):
        child_level(t, 2)


def test_a_node_moves_only_to_a_parent_of_the_same_level():
    t = Tree(ROWS)
    check_move(t, 11, 2)                      # 중분류 → 다른 대분류 아래
    with pytest.raises(ValueError, match="같은 단계"):
        check_move(t, 111, 1)                 # 소분류의 부모는 중분류여야 한다
    with pytest.raises(ValueError, match="대분류는"):
        check_move(t, 1, 11)
