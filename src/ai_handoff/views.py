"""Rich renderables for each CLI view. Nothing here reads the database or writes output."""

from collections import Counter
from datetime import date, datetime
from pathlib import Path

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ai_handoff import activity, graph, ui
from ai_handoff.diffing import word_diff
from ai_handoff.i18n import t, tn
from ai_handoff.project import Project
from ai_handoff.store import Handoff, ProjectRecord

STALE_DAYS = 14
STATS_WEEKS = 12
LIST_WEEKS = 8
BAR_WIDTH = 30
TOP_BRANCHES = 5
FIELDS = (("stack", "field.stack"), ("summary", "field.summary"), ("next_actions", "field.next_actions"))


def _content(text: str) -> RenderableType:
    if not text:
        return Text(t("field.empty"), style="muted")
    # Links are shown as text so their real target is always visible.
    return Markdown(ui.safe(text), code_theme="ansi_dark", hyperlinks=False)


def _join(*parts: Text | str | None, separator: str | None = None) -> Text:
    separator = f" {separator or ui.glyphs()['separator']} "
    return Text(separator, style="muted").join(
        part if isinstance(part, Text) else Text(part) for part in parts if part
    )


def handoff_title(handoff: Handoff, now: datetime | None = None) -> Text:
    git = ui.safe(handoff.git_label()) if handoff.git_label() else None
    return _join(
        Text(f"#{handoff.id}", style="id"),
        ui.agent_text(handoff.agent),
        Text(ui.relative_time(handoff.created_at, now), style="time"),
        Text(git, style="branch") if git else None,
    )


def handoff_panel(handoff: Handoff, now: datetime | None = None) -> Panel:
    body: list[RenderableType] = []
    for attribute, label in FIELDS:
        body += [Text(t(label), style="label"), _content(getattr(handoff, attribute)), Text()]
    footer = Text(tn("show.redactions", handoff.redactions), style="warning") if handoff.redactions else None
    return Panel(
        Group(*body[:-1]),
        title=handoff_title(handoff, now),
        title_align="left",
        subtitle=footer,
        subtitle_align="right",
        border_style=ui.agent_color(handoff.agent),
        box=box.ROUNDED,
        padding=(0, 1),
    )


def project_header(project: Project, paused: bool, last: Handoff | None, now: datetime | None) -> Text:
    header = Text(ui.safe(project.name), style="title")
    if paused:
        header.append(f"  {t('status.paused')}", style="warning")
    elif last and ui.days_since(last.created_at, now) > STALE_DAYS:
        header.append(f"  {t('status.stale', days=ui.days_since(last.created_at, now))}", style="warning")
    header.append(f"\n{project.key}\n{ui.short_path(str(project.root))}", style="muted")
    return header


def show(project: Project, paused: bool, handoff: Handoff | None, now: datetime | None = None) -> Group:
    parts: list[RenderableType] = [project_header(project, paused, handoff, now), Text()]
    if handoff:
        parts.append(handoff_panel(handoff, now))
    else:
        parts.append(Text(t("show.no_handoff"), style="muted"))
    return Group(*parts)


def history(handoffs: list[Handoff], now: datetime | None = None) -> Group:
    return Group(*(handoff_panel(handoff, now) for handoff in handoffs))


def _lane_color(name: str, by: str) -> str:
    if name in (graph.OTHER_LANE, graph.NO_BRANCH):
        return ui.MUTED_COLOR
    if by == "agent":
        return ui.RESTORE_COLOR if name == "restore" else ui.agent_color(name)
    return ui.key_color(name)


MIN_SUMMARY_WIDTH = 24


def _width(labels: dict[int, str]) -> int:
    return max((len(label) for label in labels.values()), default=0)


def log(handoffs: list[Handoff], by: str, width: int, now: datetime | None = None) -> Group:
    """A 'git log --graph' style view, newest first."""
    glyphs = ui.glyphs()
    rows, lanes = graph.layout(handoffs, graph.LANE_KEYS[by])
    colors = [_lane_color(name, by) for name in lanes]
    graph_width = 2 * len(lanes)

    legend = Text()
    for name, color in zip(lanes, colors, strict=True):
        legend.append(f"{glyphs['node']} ", style=color)
        legend.append(f"{ui.safe(name)}   ", style="muted")
    lines: list[Text] = [legend, Text()]

    agent_width = max((len(row.handoff.agent) for row in rows), default=0)
    times = [ui.relative_time(row.handoff.created_at, now) for row in rows]
    time_width = max((len(value) for value in times), default=0)
    id_width = max((len(str(row.handoff.id)) for row in rows), default=0) + 1

    # Keep room for the summary: shorten 'branch @ commit' to the commit, or drop it, when narrow.
    used = graph_width + 1 + id_width + 2 + agent_width + 2 + time_width + 2
    git_labels = {row.handoff.id: row.handoff.git_label() or "" for row in rows}
    if width - used - _width(git_labels) - 2 < MIN_SUMMARY_WIDTH:
        git_labels = {row.handoff.id: (row.handoff.git_commit or "")[:7] for row in rows}
    if width - used - _width(git_labels) - 2 < MIN_SUMMARY_WIDTH:
        git_labels = {}
    git_width = _width(git_labels)

    for row, when in zip(rows, times, strict=True):
        if row.from_lane is not None:
            cells = dict(graph.connector(row.from_lane, row.lane, glyphs))
            line = Text()
            for column in range(graph_width):
                line.append(cells.get(column, " "), style=colors[row.lane] if column in cells else "")
            lines.append(line)

        handoff = row.handoff
        line = Text(" " * (2 * row.lane))
        node = glyphs["restore"] if handoff.is_restore else glyphs["node"]
        line.append(node, style=f"bold {colors[row.lane]}")
        line.append(" " * (graph_width - 2 * row.lane))  # rest of the graph, then one space
        line.append(f"#{handoff.id}".ljust(id_width), style="id")
        line.append("  ")
        line.append(ui.safe(handoff.agent).ljust(agent_width), style=f"bold {ui.agent_color(handoff.agent)}")
        line.append("  ")
        line.append(when.ljust(time_width), style="time")
        if git_width:
            line.append("  ")
            line.append(ui.safe(git_labels[handoff.id]).ljust(git_width), style="branch")
        line.append("  ")
        summary = handoff.summary.splitlines()[0] if handoff.summary else ""
        if handoff.is_restore:
            summary = f"{t('log.restored_from', id=handoff.agent.split(':', 1)[1])} {summary}"
        line.append(ui.safe(summary))
        line.truncate(width, overflow="ellipsis")
        lines.append(line)
    return Group(*lines)


class WordDiff:
    """Word-level diff that picks colors or git-style markers for the console it is printed on."""

    def __init__(self, old: str, new: str) -> None:
        self.chunks = word_diff(ui.safe(old), ui.safe(new))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        color = console.color_system is not None
        text = Text()
        for operation, chunk in self.chunks:
            if operation == "equal":
                text.append(chunk)
            elif operation == "delete":
                text.append(chunk if color else f"[-{chunk}-]", style="diff.delete")
            else:
                text.append(chunk if color else f"{{+{chunk}+}}", style="diff.insert")
        yield text


def diff(old: Handoff, new: Handoff, now: datetime | None = None) -> Group:
    arrow = ui.glyphs()["arrow"]
    header = Text("  ")
    header.append_text(handoff_title(old, now))
    header.append(f"\n{arrow} ", style="muted")
    header.append_text(handoff_title(new, now))
    parts: list[RenderableType] = [header, Text()]

    unchanged = []
    for attribute, label in FIELDS:
        before, after = getattr(old, attribute), getattr(new, attribute)
        if before == after:
            unchanged.append(t(label))
            continue
        parts += [Text(t(label), style="label"), WordDiff(before, after), Text()]
    if old.git_label() != new.git_label():
        parts += [
            Text(t("field.git"), style="label"),
            Text(
                f"{ui.safe(old.git_label() or '-')} {arrow} {ui.safe(new.git_label() or '-')}", style="branch"
            ),
            Text(),
        ]
    if unchanged:
        parts.append(Text(t("diff.unchanged", fields=", ".join(unchanged)), style="muted"))
    if len(parts) == 2:
        parts.append(Text(t("diff.identical"), style="muted"))
    return Group(*parts)


def _bars(counts: Counter[str], color_of, glyph: str) -> list[Text]:
    total = sum(counts.values())
    peak = max(counts.values(), default=0)
    width = max((len(name) for name in counts), default=0)
    lines = []
    for name, count in counts.most_common():
        line = Text(ui.safe(name).ljust(width) + "  ", style=f"bold {color_of(name)}")
        line.append(glyph * max(1, round(count * BAR_WIDTH / peak)), style=color_of(name))
        line.append(f" {count}  ", style="bold")
        line.append(f"{round(100 * count / total)} %", style="muted")
        lines.append(line)
    return lines


def _heatmap(handoffs: list[Handoff], today: date) -> list[Text]:
    glyphs = ui.glyphs()
    levels = glyphs["levels"]
    mondays, grid = activity.heatmap(handoffs, STATS_WEEKS, today)
    peak = max((count for row in grid for count in row if count), default=0)
    days = t("weekday.short").split(",")
    margin = max(len(day) for day in days) + 1

    # Label each month at its first visible week, when its weeks leave room for the name.
    months = [" "] * (2 * len(mondays))
    starts = [
        column
        for column, monday in enumerate(mondays)
        if column == 0 or monday.month != mondays[column - 1].month
    ]
    for index, column in enumerate(starts):
        label = t("month.short").split(",")[mondays[column].month - 1]
        if index + 1 < len(starts) and len(label) >= 2 * (starts[index + 1] - column):
            continue  # it would run into the next month's label
        # The last month may run past the grid: nothing follows it.
        end = 2 * column + len(label)
        months += [" "] * max(0, end - len(months))
        months[2 * column : end] = label
    lines = [Text(" " * margin + "".join(months), style="muted")]

    for row_index, row in enumerate(grid):
        line = Text((days[row_index] if row_index in (0, 2, 4) else "").ljust(margin), style="muted")
        for count in row:
            if count is None:
                line.append("  ")
                continue
            current = activity.level(count, peak, len(levels))
            line.append(levels[current] + " ", style="green" if current else "muted")
        lines.append(line)

    legend = Text(" " * margin + t("stats.less") + " ", style="muted")
    for index, char in enumerate(levels):
        legend.append(char + " ", style="green" if index else "muted")
    legend.append(t("stats.more"), style="muted")
    lines.append(legend)
    return lines


def stats(project: Project, handoffs: list[Handoff], today: date, now: datetime | None = None) -> Group:
    if not handoffs:
        return Group(Text(ui.safe(project.name), style="title"), Text(t("show.no_handoff"), style="muted"))
    agents = Counter(handoff.agent for handoff in handoffs if not handoff.is_restore)
    branches = Counter(handoff.git_branch for handoff in handoffs if handoff.git_branch)
    first, last = handoffs[0], handoffs[-1]

    summary = _join(
        Text(ui.safe(project.name), style="title"),
        tn("count.handoffs", len(handoffs)),
        tn("count.agents", len(agents)),
        t("stats.last", when=ui.relative_time(last.created_at, now)),
    )
    details = _join(
        t("stats.first", date=ui.short_date(ui.parse_time(first.created_at), now)),
        tn("count.branches", len(branches)) if branches else None,
        tn("count.restores", sum(handoff.is_restore for handoff in handoffs))
        if any(handoff.is_restore for handoff in handoffs)
        else None,
    )
    details.stylize("muted")
    parts: list[RenderableType] = [summary, details]
    idle = ui.days_since(last.created_at, now)
    if idle > STALE_DAYS:
        parts.append(Text(t("status.stale", days=idle), style="warning"))

    bar = ui.glyphs()["bar"]
    parts += [Text(), Text(t("stats.activity", weeks=STATS_WEEKS), style="label"), *_heatmap(handoffs, today)]
    if agents:
        parts += [Text(), Text(t("stats.agents"), style="label"), *_bars(agents, ui.agent_color, bar)]
    if branches:
        top = Counter(dict(branches.most_common(TOP_BRANCHES)))
        parts += [Text(), Text(t("stats.branches"), style="label"), *_bars(top, ui.key_color, bar)]
    return Group(*parts)


def projects(
    records: list[ProjectRecord], recent: list[Handoff], today: date, now: datetime | None = None
) -> Table:
    by_project: dict[str, list[Handoff]] = {}
    for handoff in recent:
        by_project.setdefault(handoff.project_key, []).append(handoff)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column(t("list.project"))
    table.add_column(t("list.last_agent"))
    table.add_column(t("list.last_handoff"))
    table.add_column(t("list.handoffs"), justify="right")
    table.add_column(t("list.activity", weeks=LIST_WEEKS))
    table.add_column(t("list.status"))

    spark_glyphs = ui.glyphs()["spark"]
    for record in records:
        name = Text(ui.safe(record.name), style="bold")
        if Path(record.root).is_dir():
            name.append(f"\n{ui.safe(ui.short_path(record.root))}", style="muted")
        else:
            name.append(f"\n{t('list.missing_folder', key=ui.safe(record.key))}", style="warning")

        weekly = activity.weekly_counts(by_project.get(record.key, []), LIST_WEEKS, today)
        if record.paused:
            status = Text(t("status.paused"), style="warning")
        elif not record.last_handoff_at:
            status = Text(t("status.empty"), style="muted")
        elif ui.days_since(record.last_handoff_at, now) > STALE_DAYS:
            status = Text(t("status.stale", days=ui.days_since(record.last_handoff_at, now)), style="warning")
        else:
            status = Text(t("status.active"), style="success")

        table.add_row(
            name,
            ui.agent_text(record.last_agent) if record.last_agent else Text("-", style="muted"),
            Text(ui.relative_time(record.last_handoff_at, now), style="time")
            if record.last_handoff_at
            else Text("-", style="muted"),
            str(record.handoffs),
            Text(activity.sparkline(weekly, spark_glyphs), style="green"),
            status,
        )
    return table


WELCOME_GROUPS = (
    ("welcome.browse", ("show", "log", "diff", "stats", "list", "history")),
    ("welcome.record", ("save", "restore")),
    ("welcome.manage", ("pause", "resume", "purge", "render", "where")),
    ("welcome.configure", ("setup", "doctor", "completion")),
)


def welcome(
    version: str,
    project: Project | None,
    paused: bool,
    count: int,
    last: Handoff | None,
    now: datetime | None = None,
) -> Group:
    """What 'handoff' alone shows: the state of the current project, then the commands by use."""
    title = Text("Handoff", style="title")
    title.append(f" {version}  ", style="muted")
    title.append(t("help.description"), style="muted")

    if project is None:
        status = Text(t("welcome.no_project"), style="muted")
    else:
        status = Text(ui.safe(project.name), style="bold")
        if paused:
            status.append(f"  {t('status.paused')}", style="warning")
        elif last is None:
            status.append(f"  {t('welcome.empty')}", style="muted")
        else:
            status.append(f"  {tn('count.handoffs', count)} · ", style="muted")
            status.append(t("welcome.last", when=ui.relative_time(last.created_at, now)), style="muted")
            status.append(" ")
            status.append_text(ui.agent_text(last.agent))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    for index, (label, names) in enumerate(WELCOME_GROUPS):
        if index:
            grid.add_row("", "")
        grid.add_row(Text(t(label), style="label"), "")
        for name in names:
            grid.add_row(Text(f"  handoff {name}", style="bold"), Text(t(f"cmd.{name}"), style="muted"))
    return Group(title, Text(), status, Text(), grid, Text(), Text(t("welcome.footer"), style="muted"))
