"""Terminal presentation helpers: console, colors, glyphs and human-friendly times.

Colors follow NO_COLOR / FORCE_COLOR and switch off when output is not a terminal
(Rich handles both). Every piece of text written by an assistant goes through
safe() so it can never inject escape sequences into the user's terminal.
"""

import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

from ai_handoff.i18n import t, tn

THEME = Theme(
    {
        "muted": "dim",
        "id": "bold",
        "title": "bold",
        "label": "bold cyan",
        "branch": "cyan",
        "time": "dim",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "diff.delete": "red strike",
        "diff.insert": "bold green",
    }
)

# Okabe-Ito palette: distinguishable with the common forms of color blindness.
# Yellow is left out because it is unreadable on light backgrounds.
_PALETTE = ("#E69F00", "#0072B2", "#CC79A7", "#009E73", "#56B4E9", "#D55E00")
_KNOWN_AGENTS = {
    "claude": "#E69F00",
    "copilot": "#0072B2",
    "gemini": "#CC79A7",
    "codex": "#009E73",
    "cursor": "#56B4E9",
}
RESTORE_COLOR = "yellow"
MUTED_COLOR = "bright_black"

UNICODE_GLYPHS = {
    "node": "●",
    "restore": "↺",
    "vertical": "│",
    "horizontal": "─",
    "down_right": "╰",
    "right_down": "╮",
    "down_left": "╭",
    "left_up": "╯",
    "bar": "█",
    "levels": "·░▒▓█",
    "spark": "▁▂▃▄▅▆▇█",
    "arrow": "→",
    "separator": "·",
}
ASCII_GLYPHS = {
    "node": "*",
    "restore": "R",
    "vertical": "|",
    "horizontal": "-",
    "down_right": "\\",
    "right_down": ".",
    "down_left": ".",
    "left_up": "/",
    "bar": "#",
    "levels": ".:-=#",
    "spark": "_.-=#",
    "arrow": "->",
    "separator": "-",
}

# C0 and C1 control characters, except tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_console: Console | None = None
_error_console: Console | None = None


def console() -> Console:
    global _console
    if _console is None:
        _console = Console(theme=THEME, highlight=False)
    return _console


def error_console() -> Console:
    global _error_console
    if _error_console is None:
        _error_console = Console(theme=THEME, highlight=False, stderr=True)
    return _error_console


def set_console(value: Console) -> None:
    """Use this console for output and for color and glyph decisions (tests, embedding)."""
    global _console
    _console = value


def reset() -> None:
    """Forget the consoles, so the next ones see the current stdout and environment."""
    global _console, _error_console
    _console = _error_console = None


def glyphs() -> dict[str, str]:
    encoding = (console().encoding or "").lower().replace("-", "")
    return UNICODE_GLYPHS if encoding.startswith("utf") else ASCII_GLYPHS


def safe(text: str) -> str:
    return _CONTROL.sub("", text)


def short_path(path: str) -> str:
    """Show the home directory as '~' to keep paths short."""
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def agent_color(agent: str) -> str:
    if agent.startswith("restore:"):
        return RESTORE_COLOR
    lowered = agent.lower()
    for name, color in _KNOWN_AGENTS.items():
        if name in lowered:
            return color
    return key_color(agent)


def key_color(key: str) -> str:
    """A stable color for any name (an agent, a branch)."""
    if not key:
        return MUTED_COLOR
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def agent_text(agent: str) -> Text:
    return Text(safe(agent), style=f"bold {agent_color(agent)}")


def parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def short_date(moment: datetime, now: datetime | None = None) -> str:
    local = moment.astimezone()
    now = (now or datetime.now(timezone.utc)).astimezone()
    month = t("month.short").split(",")[local.month - 1]
    key = "date.day_month" if local.year == now.year else "date.day_month_year"
    return t(key, day=local.day, month=month, year=local.year)


def relative_time(value: str, now: datetime | None = None) -> str:
    moment = parse_time(value)
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return t("time.just_now")
    if seconds < 3600:
        return tn("time.minutes_ago", seconds // 60)
    if seconds < 86400:
        return tn("time.hours_ago", seconds // 3600)
    days = (now.astimezone().date() - moment.astimezone().date()).days
    if days <= 1:
        return t("time.yesterday")
    if days < 7:
        return tn("time.days_ago", days)
    return short_date(moment, now)


def days_since(value: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(0, (now - parse_time(value)).days)


def configure_streams() -> None:
    """Never crash on a console that cannot print a character."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
