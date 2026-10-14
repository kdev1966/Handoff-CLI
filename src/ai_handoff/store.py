"""SQLite storage: one row per project, and an append-only history of handoffs."""

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ai_handoff import __version__, paths
from ai_handoff.errors import HandoffError, NotFoundError, PausedError, ValidationError
from ai_handoff.i18n import t
from ai_handoff.project import Project, git_context, git_fingerprint
from ai_handoff.redact import redact

SCHEMA_VERSION = 4
MAX_FIELD_BYTES = 16 * 1024
# Above this size, memory_save still succeeds but warns: the handoff is read at every session start.
LONG_FIELD_BYTES = 4 * 1024
MAX_HISTORY = 100
BUSY_TIMEOUT_SECONDS = 10

_AGENT = re.compile(r"[\w .:/@+-]{1,64}")

# The agent name used by 'handoff save' when a person saves by hand.
CLI_AGENT = "cli"
# The agent name stored when an assistant does not give one.
UNKNOWN_AGENT = "unknown"

# Names assistants give themselves, mapped to one name per tool, so that the same tool
# is not split across several lanes and counts.
AGENT_ALIASES = {
    "claude": "claude-code",
    "claude code": "claude-code",
    "claudecode": "claude-code",
    "claude-code-cli": "claude-code",
    "gemini": "gemini-cli",
    "gemini cli": "gemini-cli",
    "copilot cli": "copilot",
    "copilot-cli": "copilot",
    "github copilot": "copilot",
    "github-copilot": "copilot",
    "codex-cli": "codex",
    "openai-codex": "codex",
    "junie-cli": "junie",
}

# Summaries that say nothing: a reminder is shown when a handoff uses one.
PLACEHOLDER_SUMMARIES = {
    "summary",
    "state",
    "status",
    "todo",
    "tbd",
    "wip",
    "test",
    "none",
    "n/a",
    "na",
    "xxx",
    "ok",
    "done",
    "-",
    "...",
    "?",
}
MIN_SUMMARY_LENGTH = 20


# Canonical names of the tools Handoff knows, matched without regard to case.
KNOWN_AGENTS = {
    *AGENT_ALIASES.values(),
    "codex",
    "cursor",
    "junie",
    "opencode",
    "cline",
    "kiro",
    "windsurf",
    "antigravity",
    "zed",
}


def canonical_agent(agent: str) -> str:
    agent = agent.strip()
    lowered = agent.lower()
    if lowered in AGENT_ALIASES:
        return AGENT_ALIASES[lowered]
    return lowered if lowered in KNOWN_AGENTS else agent


# Control characters other than tab and newline.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# MIGRATIONS[n] upgrades a database from schema n - 1 to schema n.
MIGRATIONS = {
    1: """
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
    """,
    2: """
    ALTER TABLE handoffs ADD COLUMN git_branch TEXT;
    ALTER TABLE handoffs ADD COLUMN git_commit TEXT;
    """,
    # Working tree state the memory is up to date with, and the state for which the
    # assistant was already asked to save a handoff (so it is asked only once).
    3: """
    CREATE TABLE IF NOT EXISTS project_state (
        project_key          TEXT PRIMARY KEY,
        baseline_fingerprint TEXT,
        reminded_fingerprint TEXT,
        updated_at           TEXT NOT NULL
    );
    """,
    # The handoff a session started from (0: it started with an empty memory), so that a
    # save can tell whether others were saved in between. NULL when unknown.
    4: """
    ALTER TABLE handoffs ADD COLUMN based_on INTEGER;
    """,
}


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
    git_branch: str | None = None
    git_commit: str | None = None
    based_on: int | None = None

    def __post_init__(self) -> None:
        # Older handoffs may carry an alias ('claude'): show every handoff under its tool's name.
        object.__setattr__(self, "agent", canonical_agent(self.agent))

    @property
    def is_restore(self) -> bool:
        return self.agent.startswith("restore:")

    @property
    def by_assistant(self) -> bool:
        """Written by an AI assistant, not by a person with 'handoff save' or 'handoff restore'."""
        return self.agent != CLI_AGENT and not self.is_restore

    def warnings(self) -> list[str]:
        """Message keys for a handoff that will not help whoever continues."""
        found = []
        summary = self.summary.strip()
        if len(summary) < MIN_SUMMARY_LENGTH or summary.lower().strip(" .!") in PLACEHOLDER_SUMMARIES:
            found.append("warning.summary_short")
        if not self.next_actions.strip():
            found.append("warning.no_next_actions")
        if any(len(text.encode("utf-8")) > LONG_FIELD_BYTES for text in (self.summary, self.next_actions)):
            found.append("warning.long")
        if self.agent == UNKNOWN_AGENT:
            found.append("warning.no_agent")
        return found

    def git_label(self) -> str | None:
        """'branch @ 1a2b3c4', or whichever part is known."""
        commit = self.git_commit[:7] if self.git_commit else None
        if self.git_branch and commit:
            return f"{self.git_branch} @ {commit}"
        return self.git_branch or commit

    def to_text(self, notes: tuple[str, ...] = ()) -> str:
        """Plain text for AI assistants, with optional notes under the title line."""
        where = f" on {self.git_label()}" if self.git_label() else ""
        return "\n".join(
            [
                f"Handoff #{self.id}, saved {self.created_at} by {self.agent}{where}.",
                *notes,
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
    last_agent: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_field(name: str, value: object, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValidationError(t("error.field_not_string", name=name))
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text).strip()
    if required and not text:
        raise ValidationError(t("error.field_empty", name=name))
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError(t("error.field_unicode", name=name)) from exc
    if size > MAX_FIELD_BYTES:
        raise ValidationError(t("error.field_too_large", name=name, size=size, limit=MAX_FIELD_BYTES))
    return text


def clean_agent(agent: str | None) -> str:
    agent = (agent or "").strip() or UNKNOWN_AGENT
    if not _AGENT.fullmatch(agent):
        raise ValidationError(t("error.agent_invalid"))
    return canonical_agent(agent)


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
            raise HandoffError(t("error.schema_newer", path=self.path, version=version, current=__version__))
        for target in range(version + 1, SCHEMA_VERSION + 1):
            # One transaction per step: a failed migration leaves the previous schema intact.
            self._conn.executescript(
                f"BEGIN;\n{MIGRATIONS[target]}\nPRAGMA user_version = {target};\nCOMMIT;"
            )

    # Reading

    def list_projects(self) -> list[ProjectRecord]:
        rows = self._conn.execute(f"{_PROJECT_QUERY} GROUP BY p.key {_PROJECT_ORDER}").fetchall()
        return [_record(row) for row in rows]

    def get_project(self, key: str) -> ProjectRecord | None:
        row = self._conn.execute(f"{_PROJECT_QUERY} WHERE p.key = ? GROUP BY p.key", (key,)).fetchone()
        return _record(row) if row else None

    def is_paused(self, key: str) -> bool:
        row = self._conn.execute("SELECT paused FROM projects WHERE key = ?", (key,)).fetchone()
        return bool(row and row["paused"])

    def latest(self, key: str) -> Handoff | None:
        history = self.history(key, limit=1)
        return history[0] if history else None

    def history(self, key: str, limit: int = 10) -> list[Handoff]:
        """The most recent handoffs of a project, newest first."""
        limit = max(1, min(int(limit), MAX_HISTORY))
        rows = self._conn.execute(
            "SELECT * FROM handoffs WHERE project_key = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (key, limit),
        ).fetchall()
        return [Handoff(**dict(row)) for row in rows]

    def all_handoffs(self, key: str) -> list[Handoff]:
        """Every handoff of a project, oldest first (for statistics)."""
        rows = self._conn.execute(
            "SELECT * FROM handoffs WHERE project_key = ? ORDER BY created_at, id", (key,)
        ).fetchall()
        return [Handoff(**dict(row)) for row in rows]

    def handoffs_since(self, since: str) -> list[Handoff]:
        """Handoffs of every project created at or after an ISO 8601 UTC timestamp, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM handoffs WHERE created_at >= ? ORDER BY created_at, id", (since,)
        ).fetchall()
        return [Handoff(**dict(row)) for row in rows]

    def last_id(self, key: str) -> int:
        """The number of the most recently stored handoff of a project, 0 when it has none."""
        row = self._conn.execute("SELECT MAX(id) FROM handoffs WHERE project_key = ?", (key,)).fetchone()
        return row[0] or 0

    def is_base(self, key: str, handoff_id: int) -> bool:
        """Whether a session can have started from this handoff: 0 (empty memory) or one of the project's."""
        if handoff_id == 0:
            return True
        row = self._conn.execute(
            "SELECT 1 FROM handoffs WHERE id = ? AND project_key = ?", (handoff_id, key)
        ).fetchone()
        return row is not None

    def intervening(self, handoff: Handoff) -> list[Handoff]:
        """Handoffs stored after the one this handoff is based on, and before it, newest first.

        Any of them means the handoff diverged: its author did not see them. A handoff written
        before the base (an import of older notes gets a higher number) is not new work and
        does not count.
        """
        if handoff.based_on is None:
            return []
        rows = self._conn.execute(
            """
            SELECT o.* FROM handoffs o LEFT JOIN handoffs b ON b.id = ?
            WHERE o.project_key = ? AND o.id > ? AND o.id < ?
              AND (b.id IS NULL OR o.created_at >= b.created_at)
            ORDER BY o.id DESC
            """,
            (handoff.based_on, handoff.project_key, handoff.based_on, handoff.id),
        ).fetchall()
        return [Handoff(**dict(row)) for row in rows]

    def diverged(self, key: str) -> dict[int, int]:
        """{handoff id: the id it is based on} for every handoff of a project that diverged."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT h.id, h.based_on FROM handoffs h
            JOIN handoffs o ON o.project_key = h.project_key AND o.id > h.based_on AND o.id < h.id
            LEFT JOIN handoffs b ON b.id = h.based_on
            WHERE h.project_key = ? AND h.based_on IS NOT NULL
              AND (b.id IS NULL OR o.created_at >= b.created_at)
            """,
            (key,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def fingerprints(self, key: str) -> tuple[str | None, str | None]:
        """(baseline, reminded) working tree fingerprints of a project."""
        row = self._conn.execute(
            "SELECT baseline_fingerprint, reminded_fingerprint FROM project_state WHERE project_key = ?",
            (key,),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def set_fingerprints(self, key: str, *, baseline: str | None, reminded: str | None) -> None:
        with self._conn:
            self._set_fingerprints(key, baseline, reminded)

    def _set_fingerprints(self, key: str, baseline: str | None, reminded: str | None) -> None:
        self._conn.execute(
            """
            INSERT INTO project_state (project_key, baseline_fingerprint, reminded_fingerprint, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_key) DO UPDATE SET baseline_fingerprint = excluded.baseline_fingerprint,
                reminded_fingerprint = excluded.reminded_fingerprint, updated_at = excluded.updated_at
            """,
            (key, baseline, reminded, _now()),
        )

    def get_handoff(self, handoff_id: int) -> Handoff:
        row = self._conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
        if not row:
            raise NotFoundError(t("error.handoff_not_found", id=handoff_id))
        return Handoff(**dict(row))

    def get_project_handoff(self, handoff_id: int, project: Project) -> Handoff:
        handoff = self.get_handoff(handoff_id)
        if handoff.project_key != project.key:
            raise NotFoundError(t("error.handoff_other_project", id=handoff_id, project=project.name))
        return handoff

    # Writing

    def save(
        self,
        project: Project,
        *,
        summary: str,
        next_actions: str,
        stack: str | None = None,
        agent: str | None = None,
        based_on: int | None = None,
    ) -> Handoff:
        """Append a handoff. Without a stack, the previous one is kept.

        based_on is the handoff the author started from (see is_base); the caller checks it.
        """
        if self.is_paused(project.key):
            raise PausedError(t("error.paused_not_saved", project=project.name))
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
        return self._append(project, clean_agent(agent), redactions=redactions, based_on=based_on, **fields)

    def restore(self, handoff_id: int, project: Project) -> Handoff:
        """Make an old handoff the latest again by appending a copy of it."""
        old = self.get_project_handoff(handoff_id, project)
        if self.is_paused(project.key):
            raise PausedError(t("error.paused_not_restored", project=project.name))
        return self._append(
            project,
            f"restore:{handoff_id}",
            stack=old.stack,
            summary=old.summary,
            next_actions=old.next_actions,
            redactions=old.redactions,
            based_on=None,
        )

    def set_paused(self, project: Project, paused: bool) -> None:
        with self._conn:
            self._upsert_project(project)
            self._conn.execute("UPDATE projects SET paused = ? WHERE key = ?", (int(paused), project.key))

    def purge(self, key: str) -> int:
        """Delete a project and its whole history; return the number of handoffs removed."""
        record = self.get_project(key)
        if not record:
            raise NotFoundError(t("error.project_key_not_found", key=key))
        with self._conn:
            self._conn.execute("DELETE FROM projects WHERE key = ?", (key,))
            self._conn.execute("DELETE FROM project_state WHERE project_key = ?", (key,))
        return record.handoffs

    def find_handoff(self, key: str, created_at: str, agent: str, summary: str) -> int | None:
        """The id of this exact handoff when it is already stored (used to import without duplicates)."""
        row = self._conn.execute(
            "SELECT id FROM handoffs WHERE project_key = ? AND created_at = ? AND agent = ? AND summary = ?",
            (key, created_at, agent, summary),
        ).fetchone()
        return row[0] if row else None

    def import_handoff(self, project: Project, **fields: object) -> int:
        """Store a handoff from an export as it was: same date, agent and git context.

        The project's local name and folder win over the exported ones, and the working
        tree baseline is left alone: an imported note says nothing about the code here.
        Returns the new handoff's id.
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO projects (key, name, root, created_at) VALUES (?, ?, ?, ?)",
                (project.key, project.name, str(project.root), _now()),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO handoffs (project_key, agent, stack, summary, next_actions, redactions,
                                      created_at, git_branch, git_commit)
                VALUES (:project_key, :agent, :stack, :summary, :next_actions, :redactions,
                        :created_at, :git_branch, :git_commit)
                """,
                {"project_key": project.key, **fields},
            )
        return cursor.lastrowid

    def set_based_on(self, handoff_id: int, based_on: int) -> None:
        """Link an imported handoff to the one it was based on, once both are stored."""
        with self._conn:
            self._conn.execute("UPDATE handoffs SET based_on = ? WHERE id = ?", (based_on, handoff_id))

    def _append(self, project: Project, agent: str, **fields: object) -> Handoff:
        branch, commit = git_context(project.root)
        fingerprint = git_fingerprint(project.root)
        with self._conn:
            self._upsert_project(project)
            if agent != CLI_AGENT and not agent.startswith("restore:"):
                # An assistant's handoff now describes the code as it is. A person's save or
                # restore does not: the assistant still has to document its own work.
                self._set_fingerprints(project.key, fingerprint, None)
            cursor = self._conn.execute(
                """
                INSERT INTO handoffs (project_key, agent, stack, summary, next_actions, redactions,
                                      created_at, git_branch, git_commit, based_on)
                VALUES (:project_key, :agent, :stack, :summary, :next_actions, :redactions,
                        :created_at, :git_branch, :git_commit, :based_on)
                """,
                {
                    "project_key": project.key,
                    "agent": agent,
                    "created_at": _now(),
                    "git_branch": branch,
                    "git_commit": commit,
                    **fields,
                },
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


_PROJECT_QUERY = """
SELECT p.key, p.name, p.root, p.paused,
       COUNT(h.id) AS handoffs, MAX(h.created_at) AS last_handoff_at,
       (SELECT agent FROM handoffs l WHERE l.project_key = p.key
        ORDER BY l.created_at DESC, l.id DESC LIMIT 1) AS last_agent
FROM projects p LEFT JOIN handoffs h ON h.project_key = p.key
"""
_PROJECT_ORDER = "ORDER BY last_handoff_at IS NULL, last_handoff_at DESC, p.name"


def _record(row: sqlite3.Row) -> ProjectRecord:
    last_agent = canonical_agent(row["last_agent"]) if row["last_agent"] else None
    return ProjectRecord(**{**dict(row), "paused": bool(row["paused"]), "last_agent": last_agent})


def _restrict(path: Path, mode: int) -> None:
    """Limit access to the current user. Windows relies on the per-user profile ACLs instead."""
    if sys.platform != "win32":
        os.chmod(path, mode)
