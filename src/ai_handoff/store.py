"""SQLite storage: one row per project, and an append-only history of handoffs."""

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ai_handoff import paths
from ai_handoff.errors import HandoffError, NotFoundError, PausedError, ValidationError
from ai_handoff.project import Project
from ai_handoff.redact import redact

SCHEMA_VERSION = 1
MAX_FIELD_BYTES = 16 * 1024
MAX_HISTORY = 100
BUSY_TIMEOUT_SECONDS = 10

_AGENT = re.compile(r"[\w .:/@+-]{1,64}")
# Control characters other than tab and newline.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    key        TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    root       TEXT NOT NULL,
    paused     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key  TEXT NOT NULL REFERENCES projects(key) ON DELETE CASCADE,
    agent        TEXT NOT NULL,
    stack        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    next_actions TEXT NOT NULL,
    redactions   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_project ON handoffs(project_key, id);
"""


@dataclass(frozen=True)
class Handoff:
    id: int
    project_key: str
    agent: str
    stack: str
    summary: str
    next_actions: str
    redactions: int
    created_at: str

    def to_text(self) -> str:
        return "\n".join(
            [
                f"Handoff #{self.id}, saved {self.created_at} by {self.agent}.",
                "",
                "## Stack",
                self.stack or "(not specified)",
                "",
                "## Current state",
                self.summary,
                "",
                "## Next actions",
                self.next_actions or "(none recorded)",
            ]
        )


@dataclass(frozen=True)
class ProjectRecord:
    key: str
    name: str
    root: str
    paused: bool
    handoffs: int
    last_handoff_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_field(name: str, value: object, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"'{name}' must be a string.")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text).strip()
    if required and not text:
        raise ValidationError(f"'{name}' must not be empty.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError(f"'{name}' is not valid Unicode text.") from exc
    if size > MAX_FIELD_BYTES:
        raise ValidationError(
            f"'{name}' is {size} bytes; the limit is {MAX_FIELD_BYTES}. Summarize it instead of pasting it."
        )
    return text


def clean_agent(agent: str | None) -> str:
    agent = (agent or "").strip() or "unknown"
    if not _AGENT.fullmatch(agent):
        raise ValidationError("'agent' must be 1 to 64 characters: letters, digits, spaces and . : / @ + - _")
    return agent


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.db_path()
        directory = self.path.parent
        if not directory.exists():
            directory.mkdir(parents=True)
            _restrict(directory, 0o700)
        self._conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS)
        self._conn.row_factory = sqlite3.Row
        _restrict(self.path, 0o600)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise HandoffError(
                f"{self.path} was created by a newer version of Handoff (schema {version}). Upgrade Handoff."
            )
        if version < SCHEMA_VERSION:
            with self._conn:
                self._conn.executescript(_SCHEMA)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # Reading

    def list_projects(self) -> list[ProjectRecord]:
        rows = self._conn.execute(
            """
            SELECT p.key, p.name, p.root, p.paused,
                   COUNT(h.id) AS handoffs, MAX(h.created_at) AS last_handoff_at
            FROM projects p LEFT JOIN handoffs h ON h.project_key = p.key
            GROUP BY p.key
            ORDER BY last_handoff_at IS NULL, last_handoff_at DESC, p.name
            """
        ).fetchall()
        return [_project_record(row) for row in rows]

    def get_project(self, key: str) -> ProjectRecord | None:
        row = self._conn.execute(
            """
            SELECT p.key, p.name, p.root, p.paused,
                   COUNT(h.id) AS handoffs, MAX(h.created_at) AS last_handoff_at
            FROM projects p LEFT JOIN handoffs h ON h.project_key = p.key
            WHERE p.key = ?
            GROUP BY p.key
            """,
            (key,),
        ).fetchone()
        return _project_record(row) if row else None

    def is_paused(self, key: str) -> bool:
        row = self._conn.execute("SELECT paused FROM projects WHERE key = ?", (key,)).fetchone()
        return bool(row and row["paused"])

    def latest(self, key: str) -> Handoff | None:
        history = self.history(key, limit=1)
        return history[0] if history else None

    def history(self, key: str, limit: int = 10) -> list[Handoff]:
        limit = max(1, min(int(limit), MAX_HISTORY))
        rows = self._conn.execute(
            "SELECT * FROM handoffs WHERE project_key = ? ORDER BY id DESC LIMIT ?", (key, limit)
        ).fetchall()
        return [Handoff(**dict(row)) for row in rows]

    def get_handoff(self, handoff_id: int) -> Handoff:
        row = self._conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
        if not row:
            raise NotFoundError(f"No handoff with id {handoff_id}.")
        return Handoff(**dict(row))

    # Writing

    def save(
        self,
        project: Project,
        *,
        summary: str,
        next_actions: str,
        stack: str | None = None,
        agent: str | None = None,
    ) -> Handoff:
        """Append a handoff. Without a stack, the previous one is kept."""
        if self.is_paused(project.key):
            raise PausedError(f"Handoff is paused for {project.name}; nothing was saved.")
        fields = {
            "summary": clean_field("summary", summary, required=True),
            "next_actions": clean_field("next_actions", next_actions, required=False),
        }
        if stack is None:
            previous = self.latest(project.key)
            fields["stack"] = previous.stack if previous else ""
        else:
            fields["stack"] = clean_field("stack", stack, required=False)
        redactions = 0
        for name, value in fields.items():
            fields[name], count = redact(value)
            redactions += count
        return self._append(project, clean_agent(agent), redactions=redactions, **fields)

    def restore(self, handoff_id: int, project: Project) -> Handoff:
        """Make an old handoff the latest again by appending a copy of it."""
        old = self.get_handoff(handoff_id)
        if old.project_key != project.key:
            raise NotFoundError(f"Handoff {handoff_id} does not belong to {project.name}.")
        if self.is_paused(project.key):
            raise PausedError(f"Handoff is paused for {project.name}; nothing was restored.")
        return self._append(
            project,
            f"restore:{handoff_id}",
            stack=old.stack,
            summary=old.summary,
            next_actions=old.next_actions,
            redactions=old.redactions,
        )

    def set_paused(self, project: Project, paused: bool) -> None:
        with self._conn:
            self._upsert_project(project)
            self._conn.execute("UPDATE projects SET paused = ? WHERE key = ?", (int(paused), project.key))

    def purge(self, key: str) -> int:
        """Delete a project and its whole history; return the number of handoffs removed."""
        record = self.get_project(key)
        if not record:
            raise NotFoundError(f"No project with key {key!r}.")
        with self._conn:
            self._conn.execute("DELETE FROM projects WHERE key = ?", (key,))
        return record.handoffs

    def _append(self, project: Project, agent: str, **fields: object) -> Handoff:
        created_at = _now()
        with self._conn:
            self._upsert_project(project)
            cursor = self._conn.execute(
                """
                INSERT INTO handoffs
                    (project_key, agent, stack, summary, next_actions, redactions, created_at)
                VALUES (:project_key, :agent, :stack, :summary, :next_actions, :redactions, :created_at)
                """,
                {"project_key": project.key, "agent": agent, "created_at": created_at, **fields},
            )
        return self.get_handoff(cursor.lastrowid)

    def _upsert_project(self, project: Project) -> None:
        self._conn.execute(
            """
            INSERT INTO projects (key, name, root, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET name = excluded.name, root = excluded.root
            """,
            (project.key, project.name, str(project.root), _now()),
        )


def _project_record(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        key=row["key"],
        name=row["name"],
        root=row["root"],
        paused=bool(row["paused"]),
        handoffs=row["handoffs"],
        last_handoff_at=row["last_handoff_at"],
    )


def _restrict(path: Path, mode: int) -> None:
    """Limit access to the current user. Windows relies on the per-user profile ACLs instead."""
    if sys.platform != "win32":
        os.chmod(path, mode)
