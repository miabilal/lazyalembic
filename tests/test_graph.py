from pathlib import Path

from conftest import PLAYGROUND

from lazyalembic.chain import Migration, load_chain
from lazyalembic.graph import draw


def chain_of(*pairs):
    """Migrations from (revision, parents) pairs, given newest first."""
    return [
        Migration(
            revision=rev,
            down_revisions=tuple(parents),
            message="",
            created=None,
            path=Path(f"{rev}.py"),
            is_head=False,
            is_base=not parents,
            is_branch_point=False,
            is_merge=len(parents) > 1,
        )
        for rev, parents in pairs
    ]


def test_straight_chain():
    assert draw(chain_of(("c", ["b"]), ("b", ["a"]), ("a", []))) == ["●", "●", "●"]


def test_playground_split_joins_back_at_the_branch_point():
    assert draw(load_chain(PLAYGROUND).migrations) == ["●", "│ ●", "●─╯", "●", "●"]


def test_merge_opens_a_lane_per_extra_parent():
    migrations = chain_of(
        ("merge", ["left", "right"]),
        ("left", ["base"]),
        ("right", ["base"]),
        ("base", []),
    )
    assert draw(migrations) == ["●─╮", "● │", "│ ●", "●─╯"]


def test_join_crosses_a_lane_that_keeps_going():
    migrations = chain_of(
        ("x", ["a"]),
        ("y", ["b"]),
        ("z", ["a"]),
        ("a", ["root"]),
        ("b", ["root"]),
        ("root", []),
    )
    assert draw(migrations) == ["●", "│ ●", "│ │ ●", "●─┼─╯", "│ ●", "●─╯"]


def test_three_ends_join_on_one_row():
    migrations = chain_of(("x", ["a"]), ("y", ["a"]), ("z", ["a"]), ("a", []))
    assert draw(migrations) == ["●", "│ ●", "│ │ ●", "●─╯─╯"]


def test_freed_lane_is_reused_without_shifting_others():
    migrations = chain_of(
        ("x", ["a"]),
        ("y", ["a"]),
        ("a", ["root"]),
        ("w", ["root"]),
        ("root", []),
    )
    # y's lane frees up at "a" and w reuses it
    assert draw(migrations) == ["●", "│ ●", "●─╯", "│ ●", "●─╯"]


def test_missing_parent_does_not_leave_a_dangling_lane():
    migrations = chain_of(("b", ["deleted"]), ("a", []))
    assert draw(migrations) == ["●", "●"]
