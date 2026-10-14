"""Export the memory to JSON Lines and import it back, for backups and for syncing machines.

The first line is a header; every other line is one handoff with its project. An
imported file is not trusted: every field is validated and masked again, and lines
that fail are reported and skipped. Importing twice adds nothing the second time.

Handoff numbers are local to each database: an export keeps them ("id", "based_on")
only to link each imported handoff to the one it was based on.
"""

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_handoff import __version__
from ai_handoff.errors import HandoffError, ValidationError
from ai_handoff.i18n import t
from ai_handoff.project import Project
from ai_handoff.redact import redact
from ai_handoff.store import Store, clean_agent, clean_field

FORMAT = "handoff-export"
FORMAT_VERSION = 1
MAX_KEY_LENGTH = 500
_KEY = re.compile(r"(git|path):\S.*")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")
MAX_BRANCH_LENGTH = 255


@dataclass
class ImportReport:
    imported: int = 0
    duplicates: int = 0
    projects: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)  # "line N: reason"
    path_keyed: set[str] = field(default_factory=set)  # projects whose key is a folder path


def export_lines(store: Store, keys: Iterable[str]) -> Iterator[str]:
    yield json.dumps(
        {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "handoff": __version__,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    for key in keys:
        record = store.get_project(key)
        if record is None:
            continue
        project = {"key": record.key, "name": record.name, "root": record.root}
        for handoff in store.all_handoffs(key):
            data = asdict(handoff)
            data.pop("project_key")
            yield json.dumps({"project": project, **data}, ensure_ascii=False)


def _text(value: object, name: str, *, required: bool = False, limit: int | None = None) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValidationError(t("error.field_not_string", name=name))
    if limit is not None and len(value) > limit:
        raise ValidationError(t("import.too_long", name=name))
    return value


def _created_at(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError(t("error.field_not_string", name="created_at"))
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(t("import.bad_date", value=value[:40])) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _project(data: object, override: Project | None) -> Project:
    if override is not None:
        return override
    if not isinstance(data, dict):
        raise ValidationError(t("import.no_project"))
    key = _text(data.get("key"), "project.key", required=True, limit=MAX_KEY_LENGTH)
    if not _KEY.fullmatch(key):
        raise ValidationError(t("import.bad_key", key=key[:60]))
    name = clean_field("project.name", data.get("name") or key.rsplit("/", 1)[-1], required=True)[:200]
    root = _text(data.get("root"), "project.root", limit=4096) or name
    return Project(key=key, root=Path(root), name=name)


def parse_handoff(data: Any, override: Project | None) -> tuple[Project, dict[str, object]]:
    """Validate one exported handoff; raise ValidationError with a readable reason."""
    if not isinstance(data, dict):
        raise ValidationError(t("import.not_object"))
    project = _project(data.get("project"), override)
    fields: dict[str, object] = {
        "summary": clean_field("summary", data.get("summary"), required=True),
        "next_actions": clean_field("next_actions", data.get("next_actions") or "", required=False),
        "stack": clean_field("stack", data.get("stack") or "", required=False),
    }
    redactions = 0
    for name in ("summary", "next_actions", "stack"):
        fields[name], count = redact(fields[name])
        redactions += count
    agent = data.get("agent")
    fields["agent"] = agent if isinstance(agent, str) and agent.startswith("restore:") else clean_agent(agent)
    fields["created_at"] = _created_at(data.get("created_at"))
    branch = data.get("git_branch")
    fields["git_branch"] = (
        branch
        if isinstance(branch, str) and branch.isprintable() and len(branch) <= MAX_BRANCH_LENGTH
        else None
    )
    commit = data.get("git_commit")
    fields["git_commit"] = commit if isinstance(commit, str) and _COMMIT.fullmatch(commit) else None
    for name in ("id", "based_on"):
        number = data.get(name)
        # bool is an int in Python: exclude it explicitly.
        fields[name] = (
            number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else None
        )
    previous = data.get("redactions")
    fields["redactions"] = (previous if isinstance(previous, int) and previous >= 0 else 0) + redactions
    return project, fields


def import_lines(
    lines: Iterable[str], store: Store, *, override: Project | None, dry_run: bool
) -> ImportReport:
    report = ImportReport()
    parsed: list[tuple[int, Project, dict[str, object]]] = []
    header_seen = False
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            report.errors.append(t("import.line", number=number, reason=t("import.not_json")))
            continue
        if not header_seen:
            header_seen = True
            if not isinstance(data, dict) or data.get("format") != FORMAT:
                raise HandoffError(t("import.not_export"))
            if not isinstance(data.get("version"), int) or data["version"] > FORMAT_VERSION:
                raise HandoffError(t("import.newer_format", version=data.get("version")))
            continue
        try:
            project, fields = parse_handoff(data, override)
        except ValidationError as exc:
            report.errors.append(t("import.line", number=number, reason=exc))
            continue
        parsed.append((number, project, fields))
    if not header_seen:
        raise HandoffError(t("import.not_export"))

    # Exported number -> local number, per project, to link handoffs to the one they were based on.
    local_ids: dict[tuple[str, int], int] = {}
    links: list[tuple[str, int, int]] = []  # (project key, local id, exported based_on)
    for _, project, fields in sorted(parsed, key=lambda item: str(item[2]["created_at"])):
        exported_id, based_on = fields.pop("id"), fields.pop("based_on")
        existing = store.find_handoff(
            project.key, str(fields["created_at"]), str(fields["agent"]), str(fields["summary"])
        )
        if existing is not None:
            report.duplicates += 1
            if exported_id is not None:
                local_ids[project.key, int(exported_id)] = existing
            continue
        if not dry_run:
            local_id = store.import_handoff(project, **fields)
            if exported_id is not None:
                local_ids[project.key, int(exported_id)] = local_id
            if based_on is not None:
                links.append((project.key, local_id, int(based_on)))
        report.imported += 1
        report.projects.add(project.key)
        if project.key.startswith("path:") and override is None:
            report.path_keyed.add(project.name)
    for key, local_id, based_on in links:
        # 0 means the session started with an empty memory; an unknown number leaves the link out.
        local_base = 0 if based_on == 0 else local_ids.get((key, based_on))
        if local_base is not None and local_base < local_id:
            store.set_based_on(local_id, local_base)
    return report


def projects_in(lines: Iterable[str]) -> set[str]:
    """Project keys mentioned in an export (to check that --path targets a single project)."""
    keys = set()
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict) and isinstance(project.get("key"), str):
            keys.add(project["key"])
    return keys
