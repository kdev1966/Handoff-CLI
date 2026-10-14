"""Tell whether a newer release is on PyPI, for 'handoff doctor'.

This is Handoff's only network access, and it happens only when the user runs
'handoff doctor'. HANDOFF_NO_UPDATE_CHECK=1 turns it off. Any failure (offline,
proxy, certificate) means no answer, never an error.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from ai_handoff import __version__

PACKAGE = "ai-handoff"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT_SECONDS = 3
ENV_OFF = "HANDOFF_NO_UPDATE_CHECK"
_RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

INSTALL_SCRIPT = "https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install"
UPGRADE_COMMANDS = {
    "script": f"curl -fsSL {INSTALL_SCRIPT}.sh | sh",
    "script-windows": f"irm {INSTALL_SCRIPT}.ps1 | iex",
    "pipx": f"pipx upgrade {PACKAGE}",
    "uv": f"uv tool upgrade {PACKAGE}",
    "pip": f"python -m pip install --upgrade {PACKAGE}",
}


def enabled() -> bool:
    return os.environ.get(ENV_OFF, "").strip().lower() not in ("1", "true", "yes")


def release(version: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) of a plain release; None for anything else, such as a pre-release."""
    match = _RELEASE.fullmatch(version.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def latest_release() -> str | None:
    """The latest version on PyPI, or None when it cannot be known."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=TIMEOUT_SECONDS) as response:
            version = json.load(response)["info"]["version"]
    except Exception:  # offline, proxy, certificate, unexpected answer: no answer
        return None
    return version if isinstance(version, str) else None


def install_method(prefix: str = sys.prefix, platform: str = sys.platform) -> str:
    """How this Handoff was installed, from the environment it runs in."""
    parts = [part.lower() for part in Path(prefix).parts]
    if parts[-2:] == ["handoff", "venv"]:  # where install.sh and install.ps1 put it
        return "script-windows" if platform == "win32" else "script"
    if "pipx" in parts:
        return "pipx"
    if "uv" in parts and "tools" in parts:
        return "uv"
    return "pip"


def newer_release(latest: str | None, current: str = __version__) -> str | None:
    """latest when it is a release newer than current."""
    new, old = release(latest or ""), release(current)
    return latest if new and old and new > old else None
