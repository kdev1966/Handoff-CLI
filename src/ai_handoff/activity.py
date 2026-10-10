"""Activity statistics: weekly counts, calendar heatmap and sparklines.

Weeks start on Monday and dates are in the user's local time zone.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta

from ai_handoff.store import Handoff
from ai_handoff.ui import parse_time


def local_date(handoff: Handoff) -> date:
    return parse_time(handoff.created_at).astimezone().date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def daily_counts(handoffs: Iterable[Handoff]) -> Counter[date]:
    return Counter(local_date(handoff) for handoff in handoffs)


def weekly_counts(handoffs: Iterable[Handoff], weeks: int, today: date) -> list[int]:
    """Handoffs per week, oldest first, ending with the current week."""
    first = week_start(today) - timedelta(weeks=weeks - 1)
    counts = [0] * weeks
    for day, count in daily_counts(handoffs).items():
        index = (week_start(day) - first).days // 7
        if 0 <= index < weeks:
            counts[index] += count
    return counts


def heatmap(
    handoffs: Iterable[Handoff], weeks: int, today: date
) -> tuple[list[date], list[list[int | None]]]:
    """Return the Monday of each column, and a 7-row grid (Monday first) of daily counts.

    Days after today are None.
    """
    first = week_start(today) - timedelta(weeks=weeks - 1)
    mondays = [first + timedelta(weeks=column) for column in range(weeks)]
    counts = daily_counts(handoffs)
    grid: list[list[int | None]] = [
        [
            None if monday + timedelta(days=row) > today else counts.get(monday + timedelta(days=row), 0)
            for monday in mondays
        ]
        for row in range(7)
    ]
    return mondays, grid


def level(count: int, maximum: int, levels: int = 5) -> int:
    """Map a count to 0..levels-1; zero stays 0 and the maximum reaches the top level."""
    if count <= 0 or maximum <= 0:
        return 0
    return max(1, min(levels - 1, -(-count * (levels - 1) // maximum)))  # ceiling division


def sparkline(counts: list[int], glyphs: str) -> str:
    peak = max(counts, default=0)
    if peak == 0:
        return glyphs[0] * len(counts)
    top = len(glyphs) - 1
    return "".join(glyphs[0] if count == 0 else glyphs[max(1, round(count * top / peak))] for count in counts)
