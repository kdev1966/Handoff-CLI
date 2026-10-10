"""Lay out handoffs as a graph: one lane per agent (or per git branch), one row per handoff.

Handoffs form a single chain, newest first. Consecutive handoffs in different
lanes are joined by a connector, so the graph shows who handed over to whom.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ai_handoff.store import Handoff

MAX_LANES = 6
OTHER_LANE = "…"
NO_BRANCH = "∅"


@dataclass(frozen=True)
class GraphRow:
    handoff: Handoff
    lane: int
    # Lane of the row above when it differs: a connector is drawn between them.
    from_lane: int | None


def lane_key_agent(handoff: Handoff) -> str:
    return "restore" if handoff.is_restore else handoff.agent


def lane_key_branch(handoff: Handoff) -> str:
    return handoff.git_branch or NO_BRANCH


LANE_KEYS: dict[str, Callable[[Handoff], str]] = {"agent": lane_key_agent, "branch": lane_key_branch}


def layout(
    handoffs: list[Handoff], key: Callable[[Handoff], str], max_lanes: int = MAX_LANES
) -> tuple[list[GraphRow], list[str]]:
    """Assign lanes in order of first appearance; extra keys share a final 'other' lane."""
    lanes: list[str] = []
    rows: list[GraphRow] = []
    previous: int | None = None
    for handoff in handoffs:
        name = key(handoff)
        if name not in lanes:
            if len(lanes) < max_lanes - 1:
                lanes.append(name)
            elif OTHER_LANE not in lanes:
                lanes.append(OTHER_LANE)
        lane = lanes.index(name) if name in lanes else lanes.index(OTHER_LANE)
        rows.append(GraphRow(handoff, lane, previous if previous not in (None, lane) else None))
        previous = lane
    return rows, lanes


def connector(from_lane: int, to_lane: int, glyphs: dict[str, str]) -> list[tuple[int, str]]:
    """Characters, by column, joining a node in from_lane (above) to one in to_lane (below)."""
    start, end = 2 * from_lane, 2 * to_lane
    if to_lane > from_lane:
        cells = [(start, glyphs["down_right"]), (end, glyphs["right_down"])]
    else:
        cells = [(end, glyphs["down_left"]), (start, glyphs["left_up"])]
    low, high = min(start, end), max(start, end)
    cells += [(column, glyphs["horizontal"]) for column in range(low + 1, high)]
    return sorted(cells)
