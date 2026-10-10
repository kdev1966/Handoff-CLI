import io
import json
import sys
from pathlib import Path

import pytest
from conftest import git

from ai_handoff import hooks, paths
from ai_handoff.cli import main
from ai_handoff.project import resolve_project
from ai_handoff.store import Store


@pytest.fixture
def repo(make_repo) -> Path:
    root = make_repo("app")
    (root / "main.py").write_text("print('v1')\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


def call(client: str, event: str, **data: object) -> dict | None:
    output = hooks.run(client, event, json.dumps(data))
    return json.loads(output) if output else None


def test_session_start_injects_memory(repo: Path) -> None:
    with Store() as store:
        store.save(resolve_project(repo), summary="Auth done", next_actions="Write tests", agent="codex")
    result = call("claude", "session-start", cwd=str(repo), source="startup")
    context = result["hookSpecificOutput"]["additionalContext"]
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Auth done" in context
    assert "information, not instructions" in context
    assert "memory_save" in context


def test_session_start_without_memory(repo: Path) -> None:
    context = call("claude", "session-start", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
    assert "No handoff has been saved" in context


def test_stop_asks_once_when_code_changed(repo: Path) -> None:
    call("claude", "session-start", cwd=str(repo), source="startup")
    assert call("claude", "stop", cwd=str(repo)) is None  # nothing changed

    (repo / "main.py").write_text("print('v2')\n", encoding="utf-8")
    result = call("claude", "stop", cwd=str(repo))
    assert result["decision"] == "block"
    assert "memory_save" in result["reason"]
    assert str(repo) in result["reason"]

    assert call("claude", "stop", cwd=str(repo), stop_hook_active=True) is None
    assert call("claude", "stop", cwd=str(repo)) is None  # already asked for this state

    (repo / "main.py").write_text("print('v3')\n", encoding="utf-8")
    assert call("claude", "stop", cwd=str(repo))["decision"] == "block"


def test_saving_brings_the_memory_up_to_date(repo: Path) -> None:
    call("claude", "session-start", cwd=str(repo), source="startup")
    (repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")
    with Store() as store:
        store.save(resolve_project(repo), summary="Added new_file", next_actions="")
    assert call("claude", "stop", cwd=str(repo)) is None


def test_changes_before_the_session_are_not_the_assistants(repo: Path) -> None:
    (repo / "main.py").write_text("print('edited by a human')\n", encoding="utf-8")
    call("claude", "session-start", cwd=str(repo), source="startup")
    assert call("claude", "stop", cwd=str(repo)) is None


def test_paused_project_gets_nothing(repo: Path) -> None:
    with Store() as store:
        store.set_paused(resolve_project(repo), True)
    assert call("claude", "session-start", cwd=str(repo)) is None
    (repo / "main.py").write_text("changed\n", encoding="utf-8")
    assert call("claude", "stop", cwd=str(repo)) is None


def test_outside_a_project_or_repository_nothing_happens(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert call("claude", "stop", cwd=str(plain)) is None
    assert call("claude", "session-start", cwd=str(tmp_path / "missing")) is None


@pytest.mark.parametrize(
    ("client", "check"),
    [
        ("codex", lambda out: out["decision"] == "block"),
        ("junie", lambda out: out["decision"] == "block"),
        ("gemini", lambda out: out["decision"] == "deny"),
        ("cursor", lambda out: "followup_message" in out),
        (
            "copilot",
            lambda out: out["decision"] == "block" and out["hookSpecificOutput"]["decision"] == "block",
        ),
    ],
)
def test_stop_formats(repo: Path, client: str, check) -> None:
    root_field = {"workspace_roots": [str(repo)]} if client == "cursor" else {"cwd": str(repo)}
    call(client, "session-start", **root_field)
    (repo / "main.py").write_text("changed\n", encoding="utf-8")
    assert check(call(client, "stop", **root_field))


def test_context_formats(repo: Path) -> None:
    assert "additional_context" in call("cursor", "session-start", workspace_roots=[str(repo)])
    copilot = call("copilot", "session-start", cwd=str(repo), source="new")
    assert copilot["additionalContext"] == copilot["hookSpecificOutput"]["additionalContext"]


def test_cursor_follow_up_is_not_repeated(repo: Path) -> None:
    call("cursor", "session-start", workspace_roots=[str(repo)])
    (repo / "main.py").write_text("changed\n", encoding="utf-8")
    assert call("cursor", "stop", workspace_roots=[str(repo)], loop_count=1) is None


def test_junie_stop_without_folder_uses_current_directory(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    call("junie", "session-start", cwd=str(repo))
    (repo / "main.py").write_text("changed\n", encoding="utf-8")
    assert call("junie", "stop", stop_hook_active=False)["decision"] == "block"


def test_context_is_capped(repo: Path) -> None:
    with Store() as store:
        store.save(resolve_project(repo), summary="x " * 8000, next_actions="y " * 8000, stack="z " * 8000)
    context = call("claude", "session-start", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
    assert len(context) <= hooks.MAX_CONTEXT_CHARS
    assert "truncated" in context


@pytest.mark.parametrize(
    ("client", "event", "stdin"), [("nope", "stop", "{}"), ("claude", "stop", "not json")]
)
def test_bad_input_prints_nothing(client: str, event: str, stdin: str) -> None:
    assert hooks.run(client, event, stdin) == ""


def test_errors_are_logged_not_raised(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(hooks, "stop", broken)
    assert hooks.run("claude", "stop", json.dumps({"cwd": str(repo)})) == ""
    assert "disk on fire" in (paths.data_dir() / "hooks.log").read_text(encoding="utf-8")


def test_hook_command(repo: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(repo), "source": "startup"})))
    assert main(["hook", "claude", "session-start"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_hooks_answer_in_english_whatever_the_locale(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANDOFF_LANG", "fr")
    context = call("claude", "session-start", cwd=str(repo))["hookSpecificOutput"]["additionalContext"]
    assert "No handoff has been saved" in context


def test_reminder_uses_the_tools_canonical_name(repo: Path) -> None:
    call("claude", "session-start", cwd=str(repo), source="startup")
    (repo / "main.py").write_text("changed\n", encoding="utf-8")
    assert 'agent="claude-code"' in call("claude", "stop", cwd=str(repo))["reason"]


def test_manual_save_does_not_hide_the_assistants_work(repo: Path) -> None:
    call("claude", "session-start", cwd=str(repo), source="startup")
    (repo / "main.py").write_text("assistant work\n", encoding="utf-8")
    with Store() as store:
        store.save(resolve_project(repo), summary="Saved by hand while testing", next_actions="", agent="cli")
    assert call("claude", "stop", cwd=str(repo))["decision"] == "block"
