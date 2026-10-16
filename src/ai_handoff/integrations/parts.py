"""Building blocks of an integration. Each part can apply, remove and check one change.

Kinds: "mcp" (server registered), "context" (memory injected at session start),
"save" (assistant asked to save before stopping), "instructions" (guidance in a
global instructions file) and "permissions" (tools pre-approved).
"""

import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_handoff.integrations.files import (
    ConfigError,
    Session,
    dump_json,
    has_block,
    nested,
    read_json,
    remove_block,
    upsert_block,
)

SERVER_NAME = "handoff"
HOOK_MARKER = "ai_handoff hook"
CLI_TIMEOUT_SECONDS = 60


def server_command() -> list[str]:
    """Absolute interpreter path: works even when GUI apps have a minimal PATH."""
    return [sys.executable, "-m", "ai_handoff", "serve"]


def hook_argv(client: str, event: str) -> list[str]:
    return [sys.executable, "-m", "ai_handoff", "hook", client, event]


def format_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def hook_command(client: str, event: str) -> str:
    return format_command(hook_argv(client, event))


def powershell_command(argv: list[str]) -> str:
    """PowerShell needs the call operator to run a (possibly quoted) executable path."""
    return "& " + subprocess.list2cmdline(argv)


def _is_ours(item: Any) -> bool:
    return HOOK_MARKER in json.dumps(item)


class Part:
    kind = ""

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.kind.split("+"))

    def apply(self, session: Session) -> None:
        raise NotImplementedError

    def remove(self, session: Session) -> None:
        raise NotImplementedError

    def present(self) -> bool:
        raise NotImplementedError


class JsonEntry(Part):
    """data[keys...][name] = value in a JSON file (an MCP server entry, typically)."""

    def __init__(
        self,
        path: Path,
        keys: tuple[str, ...],
        value: Callable[[], Any],
        kind: str = "mcp",
        name: str = SERVER_NAME,
    ) -> None:
        self.path, self.keys, self.value, self.kind, self.name = path, keys, value, kind, name

    def apply(self, session: Session) -> None:
        data = read_json(self.path)
        container = nested(data, self.keys, create=True)
        if container is None:
            raise ConfigError(f"{self.path}: '{'.'.join(self.keys)}' is not an object; left unchanged.")
        container[self.name] = self.value()
        session.write_text(self.path, dump_json(data), f"{self.path}: {'.'.join((*self.keys, self.name))}")

    def remove(self, session: Session) -> None:
        if not self.path.exists():
            return
        data = read_json(self.path)
        container = nested(data, self.keys, create=False)
        if container is not None and self.name in container:
            del container[self.name]
            session.write_text(self.path, dump_json(data), f"{self.path}: removed {self.name}")

    def present(self) -> bool:
        try:
            container = nested(read_json(self.path), self.keys, create=False)
        except (ConfigError, OSError):
            return False
        return container is not None and self.name in container


class JsonListItem(Part):
    """Make sure a list at data[keys...] contains value (permission rules, typically)."""

    def __init__(self, path: Path, keys: tuple[str, ...], value: str, kind: str = "permissions") -> None:
        self.path, self.keys, self.value, self.kind = path, keys, value, kind

    def _list(self, data: dict[str, Any], create: bool) -> list[Any] | None:
        parent = nested(data, self.keys[:-1], create=create)
        if parent is None:
            return None
        if self.keys[-1] not in parent and create:
            parent[self.keys[-1]] = []
        items = parent.get(self.keys[-1])
        return items if isinstance(items, list) else None

    def apply(self, session: Session) -> None:
        data = read_json(self.path)
        items = self._list(data, create=True)
        if items is None:
            raise ConfigError(f"{self.path}: '{'.'.join(self.keys)}' is not a list; left unchanged.")
        if self.value not in items:
            items.append(self.value)
            session.write_text(
                self.path, dump_json(data), f"{self.path}: {'.'.join(self.keys)} += {self.value}"
            )

    def remove(self, session: Session) -> None:
        if not self.path.exists():
            return
        data = read_json(self.path)
        items = self._list(data, create=False)
        if items and self.value in items:
            items.remove(self.value)
            session.write_text(self.path, dump_json(data), f"{self.path}: removed {self.value}")

    def present(self) -> bool:
        try:
            items = self._list(read_json(self.path), create=False)
        except (ConfigError, OSError):
            return False
        return bool(items and self.value in items)


class JsonHooks(Part):
    """Hook entries in a JSON file: data[keys...][event] is a list; ours are recognized by HOOK_MARKER."""

    def __init__(
        self,
        path: Path,
        entries: dict[str, dict[str, Any]],
        kind: str,
        keys: tuple[str, ...] = ("hooks",),
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self.path, self.entries, self.kind, self.keys, self.defaults = (
            path,
            entries,
            kind,
            keys,
            defaults or {},
        )

    def apply(self, session: Session) -> None:
        data = read_json(self.path)
        for key, value in self.defaults.items():
            data.setdefault(key, value)
        hooks = nested(data, self.keys, create=True)
        if hooks is None:
            raise ConfigError(f"{self.path}: '{'.'.join(self.keys)}' is not an object; left unchanged.")
        for event, entry in self.entries.items():
            current = hooks.get(event, [])
            if not isinstance(current, list):
                raise ConfigError(f"{self.path}: hooks for {event} are not a list; left unchanged.")
            hooks[event] = [item for item in current if not _is_ours(item)] + [entry]
        session.write_text(self.path, dump_json(data), f"{self.path}: hooks {', '.join(self.entries)}")

    def remove(self, session: Session) -> None:
        if not self.path.exists():
            return
        data = read_json(self.path)
        hooks = nested(data, self.keys, create=False)
        if hooks is None:
            return
        changed = False
        for event in self.entries:
            if isinstance(hooks.get(event), list) and any(_is_ours(item) for item in hooks[event]):
                hooks[event] = [item for item in hooks[event] if not _is_ours(item)]
                changed = True
                if not hooks[event]:
                    del hooks[event]
        if changed:
            events = ", ".join(self.entries)
            session.write_text(self.path, dump_json(data), f"{self.path}: removed hooks {events}")

    def present(self) -> bool:
        try:
            hooks = nested(read_json(self.path), self.keys, create=False)
        except (ConfigError, OSError):
            return False
        return bool(hooks) and all(
            any(_is_ours(item) for item in hooks.get(event) or []) for event in self.entries
        )


class MarkdownBlock(Part):
    """The Handoff block inside a global instructions file shared with the user."""

    kind = "instructions"

    def __init__(self, path: Path, body: Callable[[], str]) -> None:
        self.path, self.body = path, body

    def apply(self, session: Session) -> None:
        current = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        session.write_text(
            self.path, upsert_block(current, self.body()), f"{self.path}: Handoff instructions"
        )

    def remove(self, session: Session) -> None:
        if not self.path.exists():
            return
        current = self.path.read_text(encoding="utf-8")
        if not has_block(current):
            return
        remaining = remove_block(current)
        if remaining:
            session.write_text(self.path, remaining, f"{self.path}: removed Handoff instructions")
        else:
            session.delete(self.path, f"{self.path}: deleted (it only held Handoff instructions)")

    def present(self) -> bool:
        try:
            return has_block(self.path.read_text(encoding="utf-8"))
        except OSError:
            return False


class RetiredBlock(MarkdownBlock):
    """A Handoff block left by an earlier version in a file that must no longer hold it.

    Applying removes it, and deletes the file if nothing else is left.
    """

    def apply(self, session: Session) -> None:
        self.remove(session)

    def present(self) -> bool:
        return not super().present()


class OwnFile(Part):
    """A file that belongs to Handoff alone (its own rules or hooks file in a tool's folder)."""

    def __init__(self, path: Path, content: Callable[[], str], kind: str) -> None:
        self.path, self.content, self.kind = path, content, kind

    def apply(self, session: Session) -> None:
        session.write_text(self.path, self.content(), f"{self.path}: written")

    def remove(self, session: Session) -> None:
        session.delete(self.path, f"{self.path}: deleted")

    def present(self) -> bool:
        return self.path.is_file()


class CliMcp(Part):
    """Register the server with a tool's own CLI; fall back to editing its file when the CLI is missing."""

    kind = "mcp"

    def __init__(
        self,
        binary: str,
        add: Callable[[list[str]], list[str]],
        remove: list[str],
        check: Callable[[], bool],
        fallback: Part | None = None,
    ) -> None:
        self.binary, self.add, self.remove_args, self.check, self.fallback = (
            binary,
            add,
            remove,
            check,
            fallback,
        )

    def _run(self, executable: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLI_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )

    def apply(self, session: Session) -> None:
        executable = shutil.which(self.binary)
        if not executable:
            if self.fallback is None:
                raise ConfigError(f"'{self.binary}' is not on PATH.")
            self.fallback.apply(session)
            return
        args = self.add(server_command())
        session.changes.append(f"{self.binary} {' '.join(args[:3])} …")
        if session.dry_run:
            return
        self._run(executable, self.remove_args)
        result = self._run(executable, args)
        if result.returncode != 0 and self.fallback is not None:
            # Older versions of some CLIs lack the 'mcp add' command: write the file instead.
            self.fallback.apply(session)
            return
        if result.returncode != 0:
            raise ConfigError(
                f"'{self.binary} {' '.join(args[:2])}' failed: {(result.stderr or result.stdout).strip()}"
            )

    def remove(self, session: Session) -> None:
        executable = shutil.which(self.binary)
        if executable and self.check():
            session.changes.append(f"{self.binary} {' '.join(self.remove_args)}")
            if not session.dry_run:
                self._run(executable, self.remove_args)
        if self.fallback is not None:
            self.fallback.remove(session)

    def present(self) -> bool:
        try:
            return self.check()
        except (ConfigError, OSError):
            return False


class ClaudePlugin(Part):
    """Claude Code hooks shipped as a plugin in a local marketplace, installed with Claude Code's own CLI.

    Claude Code itself records the plugin; uninstalling it removes the hooks cleanly.
    """

    kind = "context+save"
    MARKETPLACE = "handoff"
    PLUGIN = "handoff"

    def __init__(self, root: Path, hooks: Callable[[], dict[str, Any]], version: str) -> None:
        self.root, self.hooks, self.version = root, hooks, version

    @property
    def plugin_id(self) -> str:
        return f"{self.PLUGIN}@{self.MARKETPLACE}"

    def _files(self) -> dict[Path, str]:
        plugin = self.root / "plugins" / self.PLUGIN
        description = (
            "Handoff: inject the project memory at session start and ask for a handoff before stopping."
        )
        return {
            self.root / ".claude-plugin" / "marketplace.json": dump_json(
                {
                    "name": self.MARKETPLACE,
                    "owner": {"name": "Handoff"},
                    "plugins": [
                        {
                            "name": self.PLUGIN,
                            "source": f"./plugins/{self.PLUGIN}",
                            "description": description,
                        }
                    ],
                }
            ),
            plugin / ".claude-plugin" / "plugin.json": dump_json(
                {"name": self.PLUGIN, "version": self.version, "description": description}
            ),
            plugin / "hooks" / "hooks.json": dump_json({"description": description, "hooks": self.hooks()}),
        }

    def _claude(self, *args: str) -> subprocess.CompletedProcess[str]:
        executable = shutil.which("claude")
        if not executable:
            raise ConfigError("'claude' is not on PATH.")
        return subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLI_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )

    def apply(self, session: Session) -> None:
        if not shutil.which("claude"):
            raise ConfigError("'claude' is not on PATH.")
        for path, content in self._files().items():
            session.write_text(path, content, f"{path}: Claude Code plugin")
        session.changes.append(f"claude plugin install {self.plugin_id}")
        if session.dry_run:
            return
        self._claude("plugin", "uninstall", self.plugin_id, "--scope", "user")
        for args in (
            ("plugin", "marketplace", "add", str(self.root), "--scope", "user"),
            ("plugin", "install", self.plugin_id, "--scope", "user"),
        ):
            result = self._claude(*args)
            if result.returncode != 0:
                raise ConfigError(
                    f"'claude {' '.join(args[:3])}' failed: {(result.stderr or result.stdout).strip()}"
                )

    def remove(self, session: Session) -> None:
        if shutil.which("claude") and self.present():
            session.changes.append(f"claude plugin uninstall {self.plugin_id}")
            if not session.dry_run:
                self._claude("plugin", "uninstall", self.plugin_id, "--scope", "user")
                self._claude("plugin", "marketplace", "remove", self.MARKETPLACE)
        for path in self._files():
            session.delete(path, f"{path}: deleted")

    def present(self) -> bool:
        try:
            result = self._claude("plugin", "list", "--json")
            plugins = json.loads(result.stdout or "[]")
        except (ConfigError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return False
        return any(isinstance(item, dict) and item.get("id") == self.plugin_id for item in plugins)
