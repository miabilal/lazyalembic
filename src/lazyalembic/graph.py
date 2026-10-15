"""Draw the migration chain as lanes, the way `git log --graph` does."""

from __future__ import annotations

from lazyalembic.chain import Migration


def draw(migrations: list[Migration]) -> list[str]:
    """One line of graph per migration, newest first.

    Each lane waits for the revision it points at. Freed lanes are reused
    rather than shifted, so lines stay straight.
    """
    known = {m.revision for m in migrations}
    lanes: list[str | None] = []
    lines = []

    for m in migrations:
        waiting = [i for i, rev in enumerate(lanes) if rev == m.revision]
        col = waiting[0] if waiting else _free_lane(lanes, after=-1)
        joins = waiting[1:]
        passing = [
            i for i, rev in enumerate(lanes)
            if rev is not None and i != col and i not in joins
        ]

        for i in joins:
            lanes[i] = None
        # a lane for a parent that isn't on disk would never close
        parents = [p for p in m.down_revisions if p in known]
        lanes[col] = parents[0] if parents else None
        opens = []
        for parent in parents[1:]:
            i = _free_lane(lanes, after=col)
            lanes[i] = parent
            opens.append(i)

        lines.append(_render(col, joins, opens, passing))
        while lanes and lanes[-1] is None:
            lanes.pop()

    return lines


def _free_lane(lanes: list[str | None], after: int) -> int:
    """Index of the first empty lane right of ``after``, adding one if needed."""
    for i in range(after + 1, len(lanes)):
        if lanes[i] is None:
            return i
    lanes.append(None)
    return len(lanes) - 1


def _render(col: int, joins: list[int], opens: list[int], passing: list[int]) -> str:
    ends = joins + opens
    span_end = max(ends, default=col)
    width = max([col, span_end, *passing]) + 1

    out = []
    for i in range(width):
        in_span = col < i < span_end
        if i == col:
            cell = "●"
        elif i in joins:
            cell = "╯"
        elif i in opens:
            cell = "╮"
        elif i in passing:
            cell = "┼" if in_span else "│"
        else:
            cell = "─" if in_span else " "
        out.append(cell)
        if i < width - 1:
            out.append("─" if col <= i < span_end else " ")
    return "".join(out).rstrip()
