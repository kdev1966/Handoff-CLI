import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ai_handoff import integrations
from ai_handoff.cli import main
from ai_handoff.integrations import clients, files, parts
from ai_handoff.integrations.files import BLOCK_BEGIN, ConfigError, Session


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every tool configuration lives in a temporary home; no real executable is ever run."""
    home = tmp_path / "home"
    home.mkdir()
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(parts.shutil, "which", lambda _name: None)
    monkeypatch.setattr(clients.shutil, "which", lambda _name: None)
    return home


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# Files


def test_markdown_block_insert_replace_remove() -> None:
    text = "# My rules\n\nKeep this.\n"
    once = files.upsert_block(text, "first")
    twice = files.upsert_block(once, "second")
    assert twice.count(BLOCK_BEGIN) == 1
    assert "second" in twice and "first" not in twice
    assert twice.startswith(text)
    assert files.remove_block(twice) == text
    assert files.remove_block(files.upsert_block("", "only")) == ""


def test_read_json_refuses_comments(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{\n  // comment\n  "a": 1\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match="not plain JSON"):
        files.read_json(path)


def test_session_backs_up_once_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("original", encoding="utf-8")
    dry = Session(dry_run=True, backup_dir=tmp_path / "backups-dry")
    dry.write_text(path, "changed", "test")
    assert path.read_text(encoding="utf-8") == "original"
    assert dry.changes == ["test"]
    assert not (tmp_path / "backups-dry").exists()

    session = Session(backup_dir=tmp_path / "backups")
    session.write_text(path, "one", "first")
    session.write_text(path, "two", "second")
    backups = list((tmp_path / "backups").rglob("config.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "original"
    assert path.read_text(encoding="utf-8") == "two"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_atomic_write_keeps_permissions(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o600)
    files.atomic_write(path, '{"a": 1}')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# Parts


def test_json_entry_keeps_other_servers(fake_home: Path) -> None:
    path = fake_home / "mcp.json"
    write_json(path, {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"})
    part = parts.JsonEntry(path, ("mcpServers",), lambda: {"command": "py"})
    part.apply(Session())
    assert part.present()
    assert load(path) == {
        "mcpServers": {"other": {"command": "x"}, "handoff": {"command": "py"}},
        "theme": "dark",
    }
    part.remove(Session())
    assert load(path) == {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}


def test_json_entry_leaves_commented_file_untouched(fake_home: Path) -> None:
    path = fake_home / "settings.json"
    path.write_text("{ /* mine */ }", encoding="utf-8")
    with pytest.raises(ConfigError):
        parts.JsonEntry(path, ("mcpServers",), dict).apply(Session())
    assert path.read_text(encoding="utf-8") == "{ /* mine */ }"


def test_json_hooks_are_idempotent_and_removable(fake_home: Path) -> None:
    path = fake_home / "hooks.json"
    theirs = {"hooks": [{"type": "command", "command": "their-script"}]}
    write_json(path, {"hooks": {"Stop": [theirs]}})
    ours = {"hooks": [{"type": "command", "command": "python -m ai_handoff hook claude stop"}]}
    part = parts.JsonHooks(path, {"Stop": ours}, kind="save")
    part.apply(Session())
    part.apply(Session())
    assert load(path)["hooks"]["Stop"] == [theirs, ours]
    assert part.present()
    part.remove(Session())
    assert load(path)["hooks"]["Stop"] == [theirs]
    assert not part.present()


def test_markdown_part_deletes_file_it_filled(fake_home: Path) -> None:
    path = fake_home / "AGENTS.md"
    part = parts.MarkdownBlock(path, lambda: "rules")
    part.apply(Session())
    part.apply(Session())
    assert path.read_text(encoding="utf-8").count(BLOCK_BEGIN) == 1
    part.remove(Session())
    assert not path.exists()


def fake_cli(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str] | None = None, fail: str | None = None):
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(argv[1:])
        joined = " ".join(argv[1:])
        code = 1 if fail and joined.startswith(fail) else 0
        stdout = next((out for prefix, out in (outputs or {}).items() if joined.startswith(prefix)), "")
        return subprocess.CompletedProcess(argv, code, stdout, "boom" if code else "")

    monkeypatch.setattr(parts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(parts.subprocess, "run", run)
    return calls


def test_cli_mcp_replaces_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    part = parts.CliMcp(
        "codex", lambda cmd: ["mcp", "add", "handoff", "--", *cmd], ["mcp", "remove", "handoff"], bool
    )
    part.apply(Session())
    assert [call[:2] for call in calls] == [["mcp", "remove"], ["mcp", "add"]]
    assert calls[1][-2:] == ["ai_handoff", "serve"]


def test_cli_mcp_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cli(monkeypatch, fail="mcp add")
    part = parts.CliMcp(
        "codex", lambda cmd: ["mcp", "add", "handoff", "--", *cmd], ["mcp", "remove", "handoff"], bool
    )
    with pytest.raises(ConfigError, match="boom"):
        part.apply(Session())


def test_cli_mcp_falls_back_to_file(fake_home: Path) -> None:
    path = fake_home / ".copilot" / "mcp-config.json"
    fallback = parts.JsonEntry(path, ("mcpServers",), lambda: {"command": "py"})
    parts.CliMcp("copilot", lambda cmd: [], [], fallback.present, fallback=fallback).apply(Session())
    assert load(path)["mcpServers"]["handoff"] == {"command": "py"}


def test_claude_plugin_lifecycle(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch, outputs={"plugin list": json.dumps([{"id": "handoff@handoff"}])})
    plugin = clients.claude_plugin()
    plugin.apply(Session())
    hooks = load(plugin.root / "plugins" / "handoff" / "hooks" / "hooks.json")["hooks"]
    command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "ai_handoff hook claude session-start" in command
    assert sys.executable in command or Path(sys.executable).name in command
    assert ["plugin", "marketplace", "add", str(plugin.root), "--scope", "user"] in calls
    assert ["plugin", "install", "handoff@handoff", "--scope", "user"] in calls
    assert plugin.present()
    plugin.remove(Session())
    assert ["plugin", "uninstall", "handoff@handoff", "--scope", "user"] in calls
    assert not (plugin.root / ".claude-plugin" / "marketplace.json").exists()


# Integrations


def test_detects_tools_from_their_folders(fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    write_json(fake_home / ".gemini" / "settings.json", {})
    names = {integration.name for integration in integrations.detected()}
    assert {"cursor", "gemini"} <= names
    assert "codex" not in names


def test_apply_and_remove_cursor(fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    cursor = clients.by_name("cursor")
    outcomes = integrations.apply([cursor], hooks=True, dry_run=False)
    assert outcomes[0].error is None
    assert load(fake_home / ".cursor" / "mcp.json")["mcpServers"]["handoff"]["args"][-2:] == [
        "ai_handoff",
        "serve",
    ]
    hooks = load(fake_home / ".cursor" / "hooks.json")
    assert hooks["version"] == 1
    assert "hook cursor stop" in hooks["hooks"]["stop"][0]["command"]
    assert integrations.status(cursor) == {"mcp": True, "context": True, "save": True, "instructions": None}

    integrations.apply([cursor], hooks=False, dry_run=False)
    assert integrations.status(cursor)["save"] is False

    integrations.remove([cursor], dry_run=False)
    assert "handoff" not in load(fake_home / ".cursor" / "mcp.json")["mcpServers"]


def test_gemini_gets_mcp_hooks_and_instructions_without_trust(fake_home: Path) -> None:
    write_json(fake_home / ".gemini" / "settings.json", {"theme": "x"})
    integrations.apply([clients.by_name("gemini")], hooks=True, dry_run=False)
    settings = load(fake_home / ".gemini" / "settings.json")
    assert settings["theme"] == "x"
    assert "trust" not in settings["mcpServers"]["handoff"]
    assert settings["hooks"]["AfterAgent"][0]["matcher"] == "*"
    assert settings["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == 20000
    assert BLOCK_BEGIN in (fake_home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")


def test_copilot_hooks_file_has_both_shells(fake_home: Path) -> None:
    (fake_home / ".copilot").mkdir()
    integrations.apply([clients.by_name("copilot")], hooks=True, dry_run=False)
    data = load(fake_home / ".copilot" / "hooks" / "handoff.json")
    entry = data["hooks"]["agentStop"][0]
    assert entry["powershell"].startswith("& ")
    assert data["version"] == 1
    assert (fake_home / ".copilot" / "instructions" / "handoff.instructions.md").exists()


def test_no_tool_is_pre_approved(fake_home: Path) -> None:
    for folder in (".cline", ".kiro"):
        (fake_home / folder).mkdir()
    integrations.apply([clients.by_name("cline"), clients.by_name("kiro")], hooks=True, dry_run=False)
    for path in (
        fake_home / ".cline" / "data" / "settings" / "cline_mcp_settings.json",
        fake_home / ".kiro" / "settings" / "mcp.json",
    ):
        assert "autoApprove" not in load(path)["mcpServers"]["handoff"]


def test_one_failing_tool_does_not_stop_the_others(fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    (fake_home / ".cursor" / "mcp.json").write_text("{ // comment }", encoding="utf-8")
    write_json(fake_home / ".gemini" / "settings.json", {})
    outcomes = integrations.apply(
        [clients.by_name("cursor"), clients.by_name("gemini")], hooks=False, dry_run=False
    )
    assert outcomes[0].error and "not plain JSON" in outcomes[0].error
    assert outcomes[1].error is None


# CLI


def run_cli(capsys, *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_setup_without_terminal_changes_nothing(capsys, fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    code, out = run_cli(capsys, "setup")
    assert code == 0
    assert "No terminal" in out
    assert not (fake_home / ".cursor" / "mcp.json").exists()


def test_setup_dry_run_previews_only(capsys, fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    code, out = run_cli(capsys, "setup", "--dry-run", "--client", "cursor")
    assert code == 0
    assert "Planned changes" in out
    assert "mcp.json" in out
    assert not (fake_home / ".cursor" / "mcp.json").exists()


def test_setup_interactive(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_home / ".cursor").mkdir()
    answers = iter(["1", "n", ""])  # all tools, no hooks, apply
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    code, out = run_cli(capsys, "setup")
    assert code == 0
    assert "Done" in out
    assert (fake_home / ".cursor" / "mcp.json").exists()
    assert not (fake_home / ".cursor" / "hooks.json").exists()


def test_setup_interactive_later(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_home / ".cursor").mkdir()
    monkeypatch.setattr("builtins.input", lambda _prompt: "3")
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    assert run_cli(capsys, "setup")[0] == 0
    assert not (fake_home / ".cursor" / "mcp.json").exists()


def test_setup_yes_then_doctor_then_remove(capsys, fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    assert run_cli(capsys, "setup", "--yes", "--client", "cursor")[0] == 0
    code, out = run_cli(capsys, "doctor")
    assert code == 0
    assert "Cursor" in out
    assert "✔" in out
    assert run_cli(capsys, "setup", "--remove", "--yes")[0] == 0
    assert "handoff" not in load(fake_home / ".cursor" / "mcp.json")["mcpServers"]
    assert run_cli(capsys, "setup", "--remove", "--yes")[1].startswith("Handoff has nothing to remove")


def test_setup_unknown_client(capsys) -> None:
    code, out = run_cli(capsys, "setup", "--yes", "--client", "nope")
    assert code == 1
    assert "Unknown tool: nope" in out
