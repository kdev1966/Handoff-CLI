"""Work out which project a path belongs to and give it a stable key."""

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ai_handoff.errors import ProjectError
from ai_handoff.i18n import t

GIT_TIMEOUT_SECONDS = 5
_SCP_LIKE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^\s]+)$")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")
MAX_BRANCH_LENGTH = 255


@dataclass(frozen=True)
class Project:
    key: str
    root: Path
    name: str


def git_output(root: Path, *args: str) -> str | None:
    """Run a read-only git command in root; return its raw output, or None on any failure."""
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
    return result.stdout if result.returncode == 0 else None


def run_git(root: Path, *args: str) -> str | None:
    """Like git_output, but stripped, and None when the output is empty."""
    output = git_output(root, *args)
    return (output or "").strip() or None


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
        raise ProjectError(t("error.unsafe_root", path=root))


def resolve_project(path: str | os.PathLike[str]) -> Project:
    """Resolve an absolute path to its project.

    The key is the normalized 'origin' remote when there is one, so the memory
    follows the repository when its folder is moved or cloned again; otherwise
    it is the folder path.
    """
    if not str(path).strip():
        raise ProjectError(t("error.path_required"))
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ProjectError(t("error.path_not_absolute", path=repr(str(path))))
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError(t("error.path_missing", path=candidate)) from exc
    if not resolved.is_dir():
        raise ProjectError(t("error.path_not_dir", path=resolved))

    root = find_root(resolved)
    check_safe_root(root)

    remote = None
    if (root / ".git").exists():
        url = run_git(root, "config", "--get", "remote.origin.url")
        remote = normalize_remote(url) if url else None
    key = f"git:{remote}" if remote else f"path:{os.path.normcase(str(root))}"
    return Project(key=key, root=root, name=root.name)


def git_context(root: Path) -> tuple[str | None, str | None]:
    """Return (branch, commit) of the repository at root, or None for what is unknown.

    A detached HEAD has no branch; a repository without commits has neither.
    """
    if not (root / ".git").exists():
        return None, None
    output = run_git(root, "rev-parse", "HEAD", "--abbrev-ref", "HEAD")
    lines = output.splitlines() if output else []
    if len(lines) != 2:
        return None, None
    commit, branch = (line.strip() for line in lines)
    if not _COMMIT.fullmatch(commit):
        commit = None
    if branch == "HEAD" or not branch.isprintable() or len(branch) > MAX_BRANCH_LENGTH:
        branch = None
    return branch or None, commit


MAX_UNTRACKED_FILES = 2000


def git_fingerprint(root: Path) -> str | None:
    """A hash of the working tree: HEAD, status, tracked changes and untracked files' size and time.

    It changes whenever the code changes, so it tells whether work happened since a
    given moment. None outside a repository or when git fails.
    """
    if not (root / ".git").exists():
        return None
    status = git_output(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status is None:
        return None
    digest = hashlib.sha256(status.encode("utf-8", "replace"))
    head = run_git(root, "rev-parse", "--verify", "-q", "HEAD")
    if head:
        digest.update(head.encode())
        diff = git_output(root, "diff", "HEAD", "--no-color", "--no-ext-diff", "--binary")
        if diff is None:
            return None
        digest.update(diff.encode("utf-8", "replace"))
    untracked = [entry[3:] for entry in status.split("\0") if entry.startswith("?? ")]
    for name in untracked[:MAX_UNTRACKED_FILES]:
        try:
            info = (root / name).stat()
        except OSError:
            continue
        digest.update(f"{name}:{info.st_size}:{info.st_mtime_ns}".encode("utf-8", "replace"))
    return digest.hexdigest()
