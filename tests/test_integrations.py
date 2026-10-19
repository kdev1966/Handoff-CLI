import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ai_handoff import completion, integrations
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
    monkeypatch.setattr(completion, "detect_shell", lambda: None)  # tests that want a shell set it
    from ai_handoff import setup_flow

    monkeypatch.setattr(setup_flow, "running_from_dev_copy", lambda: False)  # the tests run from a clone
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


def test_codegeex_keeps_the_user_servers(fake_home: Path) -> None:
    config = fake_home / ".codegeex" / "agent" / "configs" / "user_mcp_config.json"
    write_json(config, {"mcpServers": {"mine": {"command": "mine"}}})
    integration = clients.by_name("codegeex")
    assert integration.detect()
    integrations.apply([integration], hooks=True, dry_run=False)
    servers = load(config)["mcpServers"]
    assert servers["mine"] == {"command": "mine"}
    assert servers["handoff"]["args"][-1] == "serve"


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
    answers = iter(["2", "1", "n", ""])  # manual: all tools, no hooks, apply
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    code, out = run_cli(capsys, "setup")
    assert code == 0
    assert "Done" in out
    assert (fake_home / ".cursor" / "mcp.json").exists()
    assert not (fake_home / ".cursor" / "hooks.json").exists()


def test_setup_interactive_later(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_home / ".cursor").mkdir()
    answers = iter(["2", "3"])  # manual, nothing for now
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    assert run_cli(capsys, "setup")[0] == 0
    assert not (fake_home / ".cursor" / "mcp.json").exists()


def test_setup_interactive_automatic(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_home / ".cursor").mkdir()
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return ""  # Enter: the default, automatic

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    code, out = run_cli(capsys, "setup")
    assert code == 0
    assert len(prompts) == 1  # one question only
    assert "Planned changes" not in out
    assert "Done" in out
    assert (fake_home / ".cursor" / "hooks.json").exists()


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


def test_setup_automatic_installs_completion(
    capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (fake_home / ".cursor").mkdir()
    monkeypatch.setattr(completion, "detect_shell", lambda: "zsh")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    code, out = run_cli(capsys, "setup")
    assert code == 0
    assert completion.PROFILE_MARKER in (fake_home / ".zshrc").read_text(encoding="utf-8")
    assert "Tab completion installed for zsh" in out


def test_setup_manual_asks_for_completion(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_home / ".cursor").mkdir()
    monkeypatch.setattr(completion, "detect_shell", lambda: "zsh")
    prompts: list[str] = []
    answers = iter(["2", "1", "", "n", ""])  # manual, all tools, hooks yes, completion no, apply

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    assert run_cli(capsys, "setup")[0] == 0
    assert any("Tab completion" in prompt for prompt in prompts)
    assert not (fake_home / ".zshrc").exists()


def test_setup_remembers_choices_and_declined_tools(capsys, fake_home: Path, monkeypatch) -> None:
    from ai_handoff import setup_flow

    (fake_home / ".cursor").mkdir()
    (fake_home / ".kiro").mkdir()
    answers = iter(["2", "2", "", "n", "n", ""])  # manual, pick: Cursor yes, Kiro no, hooks no, apply
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    assert run_cli(capsys, "setup")[0] == 0
    choices = setup_flow.load_choices()
    assert choices == {"clients": ["cursor"], "declined": ["kiro"], "hooks": False}


def test_doctor_fix_repairs_without_reinstalling_declined_hooks(capsys, fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    assert run_cli(capsys, "setup", "--yes", "--client", "cursor", "--no-hooks")[0] == 0
    (fake_home / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    _code, out = run_cli(capsys, "doctor")
    assert "doctor --fix" in out
    assert "off" in out
    assert run_cli(capsys, "doctor", "--fix", "--yes")[0] == 0
    assert "handoff" in load(fake_home / ".cursor" / "mcp.json")["mcpServers"]
    assert not (fake_home / ".cursor" / "hooks.json").exists()
    assert "Everything is in place" in run_cli(capsys, "doctor", "--fix", "--yes")[1]


def test_doctor_fix_offers_new_tools_only(capsys, fake_home: Path, monkeypatch) -> None:
    (fake_home / ".cursor").mkdir()
    assert run_cli(capsys, "setup", "--yes", "--client", "cursor")[0] == 0
    (fake_home / ".kiro").mkdir()  # installed after the setup
    _code, out = run_cli(capsys, "doctor", "--fix", "--yes")
    assert "New AI tools found: Kiro" in out
    assert not (fake_home / ".kiro" / "settings" / "mcp.json").exists()  # never added without asking

    answers = iter(["y", ""])  # configure Kiro, apply
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    assert run_cli(capsys, "doctor", "--fix")[0] == 0
    assert (fake_home / ".kiro" / "settings" / "mcp.json").exists()


def test_doctor_fix_before_any_setup(capsys) -> None:
    code, out = run_cli(capsys, "doctor", "--fix")
    assert code == 0
    assert "not been set up" in out


def test_remove_forgets_the_choices(capsys, fake_home: Path) -> None:
    from ai_handoff import setup_flow

    (fake_home / ".cursor").mkdir()
    run_cli(capsys, "setup", "--yes", "--client", "cursor")
    run_cli(capsys, "setup", "--remove", "--yes")
    assert setup_flow.load_choices() is None


def test_cli_failure_falls_back_to_the_file(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cli(monkeypatch, fail="mcp add")  # an old Copilot CLI without 'mcp add'
    path = fake_home / ".copilot" / "mcp-config.json"
    fallback = parts.JsonEntry(path, ("mcpServers",), lambda: {"command": "py"})
    parts.CliMcp(
        "copilot", lambda cmd: ["mcp", "add", "handoff"], ["mcp", "remove", "handoff"], bool, fallback
    ).apply(Session())
    assert load(path)["mcpServers"]["handoff"] == {"command": "py"}


def test_choices_are_inferred_for_installs_older_than_0_2_0(capsys, fake_home: Path) -> None:
    from ai_handoff import setup_flow

    (fake_home / ".cursor").mkdir()
    integrations.apply([clients.by_name("cursor")], hooks=True, dry_run=False)  # set up, no setup.json
    assert setup_flow.load_choices() is None
    assert setup_flow.effective_choices() == {"clients": ["cursor"], "declined": [], "hooks": True}
    (fake_home / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    assert "doctor --fix" in run_cli(capsys, "doctor")[1]
    assert run_cli(capsys, "doctor", "--fix", "--yes")[0] == 0
    assert "handoff" in load(fake_home / ".cursor" / "mcp.json")["mcpServers"]


def test_setup_warns_from_a_development_copy(
    capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_handoff import setup_flow

    (fake_home / ".cursor").mkdir()
    monkeypatch.setattr(setup_flow, "running_from_dev_copy", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")  # Enter: the default is no
    monkeypatch.setattr(sys, "stdin", type("TTY", (io.StringIO,), {"isatty": lambda self: True})())
    code, out = run_cli(capsys, "setup")
    assert code == 1
    assert "development copy" in out
    assert not (fake_home / ".cursor" / "mcp.json").exists()


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        ('{"url": "file:///src", "dir_info": {"editable": true}}', True),
        ('{"url": "file:///src", "dir_info": {}}', False),
        ('{"url": "https://files.pythonhosted.org/x.whl", "archive_info": {}}', False),
        (None, False),
        ("not json", False),
    ],
)
def test_editable_install_detection(direct_url: str | None, expected: bool) -> None:
    from ai_handoff import setup_flow

    assert setup_flow.is_editable(direct_url) is expected


def test_notes_are_translated(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_handoff import i18n

    (fake_home / ".continue").mkdir()
    (fake_home / ".copilot").mkdir()
    monkeypatch.setenv("HANDOFF_LANG", "fr")
    monkeypatch.setenv("COLUMNS", "220")  # keep each note on one line
    i18n.set_language(None)
    out = run_cli(capsys, "doctor")[1]
    assert "sa configuration est en YAML" in out
    assert "Les hooks et la consigne" not in out or "partagés" in out


def test_every_note_and_manual_reason_is_translated() -> None:
    from ai_handoff.locales import MESSAGES

    keys = {note for integration in clients.INTEGRATIONS for note in integration.notes}
    keys |= {integration.manual for integration in clients.INTEGRATIONS if integration.manual}
    for language, catalog in MESSAGES.items():
        assert keys <= catalog.keys(), language


def test_a_targeted_first_setup_keeps_the_tools_already_configured(capsys, fake_home: Path) -> None:
    from ai_handoff import setup_flow

    (fake_home / ".cursor").mkdir()
    (fake_home / ".kiro").mkdir()
    integrations.apply([clients.by_name("cursor")], hooks=True, dry_run=False)  # configured before 0.2.0
    assert run_cli(capsys, "setup", "--yes", "--client", "kiro")[0] == 0  # first run that saves choices
    assert setup_flow.load_choices()["clients"] == ["cursor", "kiro"]
    out = run_cli(capsys, "doctor", "--fix", "--yes")[1]
    assert "New AI tools" not in out
    assert "Everything is in place" in out


def test_a_tool_configured_by_hand_since_is_not_new(capsys, fake_home: Path) -> None:
    (fake_home / ".cursor").mkdir()
    (fake_home / ".kiro").mkdir()
    assert run_cli(capsys, "setup", "--yes", "--client", "cursor")[0] == 0
    integrations.apply([clients.by_name("kiro")], hooks=True, dry_run=False)  # not through setup
    assert "New AI tools" not in run_cli(capsys, "doctor", "--fix", "--yes")[1]


# OpenCode rules file


def opencode_setup(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for name in ("OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"):
        monkeypatch.delenv(name, raising=False)
    (fake_home / ".config" / "opencode").mkdir(parents=True)
    return fake_home / ".config" / "opencode" / "AGENTS.md", fake_home / ".claude" / "CLAUDE.md"


def write_claude(claude: Path, text: str = "# My rules\n\nKeep this.\n") -> str:
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(text, encoding="utf-8")
    return text


def test_opencode_without_claude_md_uses_agents_md(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents, _claude = opencode_setup(fake_home, monkeypatch)
    opencode = clients.by_name("opencode")
    assert integrations.apply([opencode], hooks=True, dry_run=False)[0].error is None
    assert BLOCK_BEGIN in agents.read_text(encoding="utf-8")
    integrations.remove([opencode], dry_run=False)
    assert not agents.exists()


def test_opencode_block_goes_into_claude_md_when_agents_md_is_missing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents, claude = opencode_setup(fake_home, monkeypatch)
    original = write_claude(claude)
    opencode = clients.by_name("opencode")
    assert integrations.apply([opencode], hooks=True, dry_run=False)[0].error is None
    assert files.has_block(claude.read_text(encoding="utf-8"))
    assert not agents.exists()
    assert integrations.status(opencode)["instructions"] is True
    integrations.remove([opencode], dry_run=False)
    assert claude.read_text(encoding="utf-8") == original
    assert not agents.exists()


def test_opencode_migrates_a_handoff_only_agents_md_from_0_3_1(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents, claude = opencode_setup(fake_home, monkeypatch)
    write_claude(claude)
    agents.write_text(files.upsert_block("", clients.INSTRUCTIONS), encoding="utf-8")
    opencode = clients.by_name("opencode")
    first = integrations.apply([opencode], hooks=True, dry_run=False)[0]
    assert first.error is None
    assert not agents.exists()
    assert files.has_block(claude.read_text(encoding="utf-8"))
    assert integrations.status(opencode)["instructions"] is True
    after_first = claude.read_text(encoding="utf-8")
    second = integrations.apply([opencode], hooks=True, dry_run=False)[0]
    assert second.error is None
    assert not any("AGENTS.md" in change or "CLAUDE.md" in change for change in second.changes)
    assert claude.read_text(encoding="utf-8") == after_first
    assert not agents.exists()


@pytest.mark.parametrize("with_block", [False, True])
def test_opencode_keeps_a_users_agents_md_and_leaves_claude_md_alone(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, with_block: bool
) -> None:
    agents, claude = opencode_setup(fake_home, monkeypatch)
    write_claude(claude)
    before = claude.read_bytes()
    user_text = "# OpenCode rules\n\nMine.\n"
    agents.write_text(files.upsert_block(user_text, "old") if with_block else user_text, encoding="utf-8")
    opencode = clients.by_name("opencode")
    assert integrations.apply([opencode], hooks=True, dry_run=False)[0].error is None
    text = agents.read_text(encoding="utf-8")
    assert files.has_block(text) and text.startswith(user_text)
    assert claude.read_bytes() == before


def test_opencode_claude_fallback_disabled_uses_agents_md(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents, claude = opencode_setup(fake_home, monkeypatch)
    write_claude(claude)
    before = claude.read_bytes()
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE", "1")
    assert integrations.apply([clients.by_name("opencode")], hooks=True, dry_run=False)[0].error is None
    assert BLOCK_BEGIN in agents.read_text(encoding="utf-8")
    assert claude.read_bytes() == before
