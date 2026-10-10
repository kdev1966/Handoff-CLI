"""Work out which project a path belongs to and give it a stable key."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ai_handoff.errors import ProjectError

GIT_TIMEOUT_SECONDS = 5
_SCP_LIKE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^\s]+)$")


@dataclass(frozen=True)
class Project:
    key: str
    root: Path
    name: str


def run_git(root: Path, *args: str) -> str | None:
    """Run a read-only git command in root; return its stripped output or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def normalize_remote(url: str) -> str | None:
    """Turn a git remote URL into 'host/owner/repo', dropping credentials, ports and '.git'.

    Returns None for remotes without a host (local paths), which are not stable keys.
    """
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme == "file":
            return None
        host, path = parts.hostname, parts.path
    else:
        match = _SCP_LIKE.match(url)
        if not match or len(match["host"]) == 1:  # "C:\..." is a Windows path, not a host
            return None
        host, path = match["host"], match["path"]
    if not host:
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if not path:
        return None
    return f"{host}/{path}".lower()


def find_root(start: Path) -> Path:
    """Return the closest directory at or above start that contains '.git'.

    The search never climbs to the home directory or above it, so a dotfiles
    repository in $HOME does not swallow every project. Without a repository,
    start itself is the root.
    """
    home = Path.home().resolve()
    for candidate in (start, *start.parents):
        if candidate == home or (candidate in home.parents):
            break
        if (candidate / ".git").exists():
            return candidate
    return start


def check_safe_root(root: Path) -> None:
    home = Path.home().resolve()
    if root.parent == root or root == home or root in home.parents:
        raise ProjectError(
            f"Refusing to use {root} as a project: it is the home directory, a filesystem root, "
            "or contains the home directory. Pass the path of the project folder itself."
        )


def resolve_project(path: str | os.PathLike[str]) -> Project:
    """Resolve an absolute path to its project.

    The key is the normalized 'origin' remote when there is one, so the memory
    follows the repository when its folder is moved or cloned again; otherwise
    it is the folder path.
    """
    if not str(path).strip():
        raise ProjectError("A project path is required.")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProjectError(f"The project path must be absolute, got {str(path)!r}.")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError(f"The project path does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise ProjectError(f"The project path is not a directory: {resolved}")

    root = find_root(resolved)
    check_safe_root(root)

    remote = None
    if (root / ".git").exists():
        url = run_git(root, "config", "--get", "remote.origin.url")
        remote = normalize_remote(url) if url else None
    key = f"git:{remote}" if remote else f"path:{os.path.normcase(str(root))}"
    return Project(key=key, root=root, name=root.name)
