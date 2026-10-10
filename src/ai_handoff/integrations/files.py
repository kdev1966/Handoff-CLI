"""Careful edits of other tools' configuration files.

Rules: back up before the first change, write atomically while keeping the file's
permissions, keep every key we do not own, and refuse to rewrite a file we cannot
parse exactly (for example JSON with comments) rather than risk losing content.
"""

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_handoff import paths
from ai_handoff.errors import HandoffError

BLOCK_BEGIN = "<!-- handoff:begin (managed by Handoff; edits inside are overwritten) -->"
BLOCK_END = "<!-- handoff:end -->"
_BLOCK = re.compile(re.escape(BLOCK_BEGIN.split(" (")[0]) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL)


class ConfigError(HandoffError):
    """A configuration file cannot be changed safely."""


@dataclass
class Session:
    """One run of setup or removal: where backups go, and what changed."""

    dry_run: bool = False
    backup_dir: Path = field(
        default_factory=lambda: paths.data_dir() / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    changes: list[str] = field(default_factory=list)
    _backed_up: set[Path] = field(default_factory=set)

    def backup(self, path: Path) -> None:
        if self.dry_run or path in self._backed_up or not path.exists():
            return
        relative = str(path.resolve()).replace(":", "").lstrip("/\\")
        target = self.backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        self._backed_up.add(path)

    def write_text(self, path: Path, content: str, description: str) -> None:
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            return
        self.changes.append(description)
        if self.dry_run:
            return
        self.backup(path)
        atomic_write(path, content)

    def delete(self, path: Path, description: str) -> None:
        if not path.exists():
            return
        self.changes.append(description)
        if self.dry_run:
            return
        self.backup(path)
        path.unlink()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".handoff-tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        if path.exists():
            shutil.copymode(path, tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object; a missing or empty file is an empty object."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not plain JSON (comments?); it was left unchanged.") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object; it was left unchanged.")
    return data


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def nested(data: dict[str, Any], keys: tuple[str, ...], *, create: bool) -> dict[str, Any] | None:
    """The object at data[k1][k2]..., created when asked; None if absent or not an object."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        if key not in current:
            if not create:
                return None
            current[key] = {}
        current = current[key]
    return current if isinstance(current, dict) else None


def upsert_block(text: str, body: str) -> str:
    """Insert or replace the Handoff block in a Markdown text, keeping everything else."""
    block = f"{BLOCK_BEGIN}\n{body.strip()}\n{BLOCK_END}\n"
    if _BLOCK.search(text):
        return _BLOCK.sub(lambda _match: block, text, count=1)
    separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return f"{text}{separator}{block}"


def remove_block(text: str) -> str:
    stripped = _BLOCK.sub("", text)
    return stripped.rstrip("\n") + "\n" if stripped.strip() else ""


def has_block(text: str) -> bool:
    return bool(_BLOCK.search(text))
