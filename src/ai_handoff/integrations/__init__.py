"""Configuration of AI coding assistants: MCP connection, hooks and instructions.

Nothing here runs on its own: `handoff setup` applies what the developer chose,
after showing a preview, and `handoff setup --remove` takes everything back.
"""

import json
import subprocess
from dataclasses import dataclass, field

from ai_handoff.errors import HandoffError
from ai_handoff.integrations.clients import HOOK_KINDS, INTEGRATIONS, Integration, by_name
from ai_handoff.integrations.files import Session
from ai_handoff.integrations.parts import SERVER_NAME, Part, server_command

KINDS = ("mcp", "context", "save", "instructions")

__all__ = [
    "INTEGRATIONS",
    "KINDS",
    "Integration",
    "Outcome",
    "apply",
    "by_name",
    "detected",
    "mcp_config_json",
    "remove",
    "status",
]


@dataclass
class Outcome:
    integration: Integration
    changes: list[str] = field(default_factory=list)
    error: str | None = None


def detected() -> list[Integration]:
    return [integration for integration in INTEGRATIONS if _safe(integration.detect)]


def _safe(check) -> bool:  # type: ignore[no-untyped-def]
    try:
        return bool(check())
    except Exception:  # detection must never fail the command
        return False


def _is_hook(part: Part) -> bool:
    return any(kind in HOOK_KINDS for kind in part.kinds)


def apply(integrations: list[Integration], *, hooks: bool, dry_run: bool) -> list[Outcome]:
    """Configure each integration; without hooks, any hooks Handoff installed earlier are removed."""
    session = Session(dry_run=dry_run)
    outcomes = []
    for integration in integrations:
        outcome = Outcome(integration)
        if integration.manual:
            outcomes.append(outcome)
            continue
        before = len(session.changes)
        try:
            for part in integration.parts():
                if _is_hook(part) and not hooks:
                    if part.present():
                        part.remove(session)
                else:
                    part.apply(session)
        except (HandoffError, OSError, subprocess.SubprocessError) as exc:
            outcome.error = str(exc)
        outcome.changes = session.changes[before:]
        outcomes.append(outcome)
    return outcomes


def remove(integrations: list[Integration], *, dry_run: bool) -> list[Outcome]:
    session = Session(dry_run=dry_run)
    outcomes = []
    for integration in integrations:
        outcome = Outcome(integration)
        before = len(session.changes)
        for part in integration.parts():
            try:
                part.remove(session)
            except (HandoffError, OSError, subprocess.SubprocessError) as exc:
                outcome.error = str(exc)
        outcome.changes = session.changes[before:]
        outcomes.append(outcome)
    return outcomes


def status(integration: Integration) -> dict[str, bool | None]:
    """For each kind: True (in place), False (missing) or None (not applicable to this client)."""
    result: dict[str, bool | None] = dict.fromkeys(KINDS)
    for part in integration.parts():
        present = _safe(part.present)
        for kind in part.kinds:
            if kind in result:
                result[kind] = present if result[kind] is None else (result[kind] and present)
    return result


def mcp_config_json() -> str:
    command, *args = server_command()
    return json.dumps({"mcpServers": {SERVER_NAME: {"command": command, "args": args}}}, indent=2)
