"""Location of Handoff's own data, following each platform's conventions."""

import os
import sys
from pathlib import Path

ENV_HOME = "HANDOFF_HOME"
DB_FILENAME = "memory.db"


def data_dir() -> Path:
    """Return the directory that holds the database.

    HANDOFF_HOME overrides the default, which is:
    - Windows: %LOCALAPPDATA%\\handoff
    - macOS:   ~/Library/Application Support/handoff
    - Linux:   $XDG_DATA_HOME/handoff, or ~/.local/share/handoff
    """
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "handoff" if base else Path.home() / "AppData" / "Local" / "handoff"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "handoff"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "handoff"


def db_path() -> Path:
    return data_dir() / DB_FILENAME
