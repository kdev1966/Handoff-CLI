import io
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from rich.console import Console

from ai_handoff import activity, graph, i18n, ui, views
from ai_handoff.diffing import word_diff
from ai_handoff.project import Project
from ai_handoff.store import Handoff, ProjectRecord

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def handoff(id: int, agent: str = "claude-code", hours_ago: float = 1, **fields) -> Handoff:
    values = {
        "id": id,
        "project_key": "path:/p",
        "agent": agent,
        "stack": "Python",
        "summary": f"summary {id}",
        "next_actions": "next",
        "redactions": 0,
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
    }
    values.update(fields)
    return Handoff(**values)


def render(renderable, width: int = 100, color: bool = False) -> str:
    console = Console(
        width=width, theme=ui.THEME, color_system="truecolor" if color else None, force_terminal=color
    )
    ui.set_console(console)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


# Graph layout


def test_layout_assigns_lanes_in_order_of_appearance() -> None:
    rows, lanes = graph.layout(
        [handoff(4, "claude"), handoff(3, "copilot"), handoff(2, "claude"), handoff(1, "gemini")],
        graph.lane_key_agent,
    )
    assert lanes == ["claude-code", "copilot", "gemini-cli"]
    assert [(row.lane, row.from_lane) for row in rows] == [(0, None), (1, 0), (0, 1), (2, 0)]


def test_layout_groups_extra_lanes() -> None:
    handoffs = [handoff(i, f"agent{i}") for i in range(10, 0, -1)]
    rows, lanes = graph.layout(handoffs, graph.lane_key_agent, max_lanes=3)
    assert lanes == ["agent10", "agent9", graph.OTHER_LANE]
    assert {row.lane for row in rows[2:]} == {2}


def test_layout_by_branch_and_restore_lane() -> None:
    _rows, lanes = graph.layout(
        [handoff(2, "restore:1", git_branch="main"), handoff(1, git_branch=None)], graph.lane_key_branch
    )
    assert lanes == ["main", graph.NO_BRANCH]
    _, agent_lanes = graph.layout([handoff(2, "restore:1")], graph.lane_key_agent)
    assert agent_lanes == ["restore"]


@pytest.mark.parametrize(
    ("from_lane", "to_lane", "expected"), [(0, 1, "╰─╮"), (1, 0, "╭─╯"), (0, 2, "╰───╮"), (2, 1, "  ╭─╯")]
)
def test_connector_shapes(from_lane: int, to_lane: int, expected: str) -> None:
    cells = dict(graph.connector(from_lane, to_lane, ui.UNICODE_GLYPHS))
    drawn = "".join(cells.get(column, " ") for column in range(max(cells) + 1))
    assert drawn == expected


# Word diff


def test_word_diff_reconstructs_both_texts() -> None:
    old, new = "Auth module done, tests pending.", "Auth module done, tests written and green."
    chunks = word_diff(old, new)
    assert "".join(text for op, text in chunks if op != "insert") == old
    assert "".join(text for op, text in chunks if op != "delete") == new
    assert ("insert", "written and green") in chunks


def test_word_diff_identical() -> None:
    assert word_diff("same", "same") == [("equal", "same")]


# Activity


def test_weekly_counts_and_sparkline() -> None:
    today = date(2026, 10, 10)  # a Saturday
    handoffs = [handoff(1, hours_ago=1), handoff(2, hours_ago=2), handoff(3, hours_ago=24 * 8)]
    counts = activity.weekly_counts(handoffs, 4, today)
    assert counts[-1] == 2
    assert counts[-2] == 1
    assert sum(counts) == 3
    assert activity.sparkline([0, 1, 2, 4], "▁▂▃▄▅▆▇█") == "▁▃▅█"
    assert activity.sparkline([0, 0], "_#") == "__"


def test_heatmap_hides_future_days() -> None:
    today = date(2026, 10, 7)  # a Wednesday
    mondays, grid = activity.heatmap([], 2, today)
    assert mondays == [date(2026, 9, 28), date(2026, 10, 5)]
    assert grid[2][1] == 0  # today
    assert grid[3][1] is None  # tomorrow


@pytest.mark.parametrize(
    ("count", "maximum", "expected"), [(0, 5, 0), (1, 1, 4), (1, 8, 1), (8, 8, 4), (4, 8, 2)]
)
def test_levels(count: int, maximum: int, expected: int) -> None:
    assert activity.level(count, maximum) == expected


# Rendering


def test_safe_removes_escape_sequences() -> None:
    assert ui.safe("ok\x1b[2J\x07\x9b done\n\ttab") == "ok[2J done\n\ttab"


def test_log_renders_graph_and_details() -> None:
    handoffs = [
        handoff(3, "copilot", 1, git_branch="feature/auth", git_commit="a1b2c3d4e5f6"),
        handoff(2, "claude-code", 30),
        handoff(1, "restore:7", 50),
    ]
    out = render(views.log(handoffs, "agent", 100, NOW))
    assert "feature/auth @ a1b2c3d" in out
    assert "╰─╮" in out or "╭─╯" in out
    assert "restores #7" in out
    assert "↺" in out


def test_log_truncates_long_lines() -> None:
    out = render(views.log([handoff(1, summary="x" * 500)], "agent", 60, NOW), width=60)
    assert all(len(line) <= 60 for line in out.splitlines())


def test_diff_uses_markers_without_color() -> None:
    old = handoff(1, summary="tests pending")
    new = handoff(2, summary="tests green", stack="Python")
    out = render(views.diff(old, new, NOW))
    assert "[-pending-]" in out
    assert "{+green+}" in out
    assert "Unchanged: Stack" in out


def test_diff_uses_color_in_a_terminal() -> None:
    out = render(views.diff(handoff(1, summary="a"), handoff(2, summary="b"), NOW), color=True)
    assert "[-" not in out
    assert "\x1b[" in out


def test_agent_content_cannot_inject_markup_or_escapes() -> None:
    evil = handoff(1, agent="claude", summary="[bold red]boom[/] \x1b[31mred")
    out = render(views.handoff_panel(evil, NOW), color=True)
    assert "[bold red]boom[/]" in out
    assert "\x1b[31mred" not in out


def test_stats_view(tmp_path) -> None:
    project = Project(key="path:/p", root=tmp_path, name="api")
    handoffs = [
        handoff(1, "claude-code", 24 * 20, git_branch="main"),
        handoff(2, "copilot", 24 * 3, git_branch="feature/x"),
        handoff(3, "claude-code", 2, git_branch="feature/x"),
    ]
    out = render(views.stats(project, handoffs, date(2026, 10, 10), NOW))
    assert "3 handoffs" in out
    assert "2 agents" in out
    assert "claude-code" in out
    assert "67 %" in out
    assert "feature/x" in out
    assert "Mon" in out


def test_stats_flags_idle_project(tmp_path) -> None:
    project = Project(key="path:/p", root=tmp_path, name="api")
    out = render(views.stats(project, [handoff(1, hours_ago=24 * 30)], date(2026, 10, 10), NOW))
    assert "idle for 30 days" in out


def test_projects_table(tmp_path) -> None:
    records = [
        ProjectRecord(
            "path:/a", "alpha", str(tmp_path), False, 2, (NOW - timedelta(hours=1)).isoformat(), "copilot"
        ),
        ProjectRecord("git:gone", "beta", str(tmp_path / "missing"), True, 1, None, None),
    ]
    out = render(
        views.projects(records, [replace(handoff(1), project_key="path:/a")], date(2026, 10, 10), NOW),
        width=140,
    )
    assert "alpha" in out
    assert "copilot" in out
    assert "paused" in out
    assert "folder not found; key: git:gone" in out


def test_french_rendering(tmp_path) -> None:
    i18n.set_language("fr")
    project = Project(key="path:/p", root=tmp_path, name="api")
    out = render(views.stats(project, [handoff(1, hours_ago=3)], date(2026, 10, 10), NOW))
    assert "1 passation" in out
    assert "il y a 3 heures" in out
    assert "lun." in out


def test_ascii_glyphs_when_console_is_not_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(ui, "console", lambda: Console(file=stream))
    assert ui.glyphs() is ui.ASCII_GLYPHS


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0, "just now"), (0.5, "30 minutes ago"), (1, "1 hour ago"), (26, "yesterday"), (24 * 3, "3 days ago")],
)
def test_relative_time(hours: float, expected: str) -> None:
    assert ui.relative_time((NOW - timedelta(hours=hours)).isoformat(), NOW) == expected


def test_relative_time_falls_back_to_a_date() -> None:
    assert ui.relative_time("2026-01-05T10:00:00+00:00", NOW) == "Jan 5"
    assert ui.relative_time("2025-01-05T10:00:00+00:00", NOW) == "Jan 5, 2025"


def test_log_shortens_git_labels_when_narrow() -> None:
    handoffs = [
        handoff(
            2,
            "copilot",
            1,
            git_branch="feature/very-long-branch-name",
            git_commit="a1b2c3d4e5f6",
            summary="Wide summary here",
        ),
        handoff(
            1,
            "claude-code",
            2,
            git_branch="feature/very-long-branch-name",
            git_commit="0f9e8d7c6b5a",
            summary="Another summary",
        ),
    ]
    wide = render(views.log(handoffs, "agent", 160, NOW), width=160)
    assert "feature/very-long-branch-name @ a1b2c3d" in wide
    medium = render(views.log(handoffs, "agent", 80, NOW), width=80)
    assert "feature/very-long-branch-name" not in medium
    assert "a1b2c3d" in medium
    assert "Wide summary here" in medium
    narrow = render(views.log(handoffs, "agent", 60, NOW), width=60)
    assert "a1b2c3d" not in narrow
    assert "Wide summary" in narrow


def test_heatmap_labels_the_current_month(tmp_path) -> None:
    project = Project(key="path:/p", root=tmp_path, name="api")
    out = render(views.stats(project, [handoff(1, hours_ago=2)], date(2026, 10, 11), NOW))
    months_line = next(line for line in out.splitlines() if "Sep" in line)
    assert "Oct" in months_line
