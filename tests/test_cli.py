import io
import json
import sys
from pathlib import Path

import pytest

from ai_handoff import i18n
from ai_handoff.cli import main
from ai_handoff.context_file import context_path
from ai_handoff.integrations.parts import server_command
from ai_handoff.project import Project


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_save_show_history(capsys, project: Project) -> None:
    path = str(project.root)
    assert run(capsys, "save", "-p", path, "-s", "First", "-n", "Do X", "--stack", "Go")[0] == 0
    assert run(capsys, "save", "-p", path, "-s", "Second")[0] == 0

    code, out, _ = run(capsys, "show", "-p", path)
    assert code == 0
    assert "Second" in out
    assert "Go" in out  # stack kept

    code, out, _ = run(capsys, "history", "-p", path, "-l", "5")
    assert out.index("Second") < out.index("First")
    assert context_path(project).exists()


def test_save_reads_stdin(capsys, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("line one\nline two\n"))
    run(capsys, "save", "-p", str(project.root), "-s", "-")
    out = run(capsys, "show", "-p", str(project.root))[1]
    assert "line one" in out
    assert "line two" in out


def test_default_path_is_current_directory(capsys, project: Project, monkeypatch) -> None:
    monkeypatch.chdir(project.root)
    run(capsys, "save", "-s", "here")
    assert project.name in run(capsys, "list")[1]


def test_errors_exit_with_code_1(capsys, project: Project) -> None:
    code, _, err = run(capsys, "save", "-p", str(project.root), "-s", " ")
    assert code == 1
    assert "handoff: error:" in err


def test_pause_resume(capsys, project: Project) -> None:
    path = str(project.root)
    run(capsys, "save", "-p", path, "-s", "state")
    run(capsys, "pause", "-p", path)
    assert not context_path(project).exists()
    assert "paused" in run(capsys, "list")[1]
    assert run(capsys, "save", "-p", path, "-s", "blocked")[0] == 1
    run(capsys, "resume", "-p", path)
    assert context_path(project).exists()


def test_purge_requires_confirmation(capsys, project: Project, monkeypatch) -> None:
    path = str(project.root)
    run(capsys, "save", "-p", path, "-s", "state")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert run(capsys, "purge", "-p", path)[0] == 1
    assert project.name in run(capsys, "list")[1]
    assert run(capsys, "purge", "-p", path, "--yes")[0] == 0
    assert run(capsys, "list")[1].startswith("No project yet.")
    assert not context_path(project).exists()


def test_purge_by_key_for_deleted_folder(capsys, project: Project) -> None:
    run(capsys, "save", "-p", str(project.root), "-s", "state")
    project.root.rename(project.root.with_name("gone"))
    assert run(capsys, "purge", "--key", project.key, "-y")[0] == 0


def test_restore(capsys, project: Project) -> None:
    path = str(project.root)
    run(capsys, "save", "-p", path, "-s", "good")
    run(capsys, "save", "-p", path, "-s", "bad")
    assert run(capsys, "restore", "1", "-p", path)[0] == 0
    assert "good" in run(capsys, "show", "-p", path)[1]


def test_setup_print_config(capsys) -> None:
    config = json.loads(run(capsys, "setup", "--print-config")[1])
    server = config["mcpServers"]["handoff"]
    assert [server["command"], *server["args"]] == server_command()


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "handoff" in capsys.readouterr().out


def test_log_diff_stats(capsys, project: Project) -> None:
    path = str(project.root)
    run(capsys, "save", "-p", path, "-s", "tests pending", "-a", "claude-code")
    run(capsys, "save", "-p", path, "-s", "tests green", "-a", "copilot")

    code, out, _ = run(capsys, "log", "-p", path)
    assert code == 0
    assert out.index("copilot") < out.index("claude-code", out.index("copilot"))

    code, out, _ = run(capsys, "diff", "-p", path)
    assert code == 0
    assert "[-pending-]" in out
    assert "{+green+}" in out

    code, out, _ = run(capsys, "stats", "-p", path)
    assert code == 0
    assert "2 handoffs" in out


def test_diff_needs_two_handoffs(capsys, project: Project) -> None:
    run(capsys, "save", "-p", str(project.root), "-s", "only one")
    code, _, err = run(capsys, "diff", "-p", str(project.root))
    assert code == 1
    assert "Two handoffs are needed" in err


@pytest.mark.parametrize("command", ["show", "history", "log", "stats"])
def test_json_output(capsys, project: Project, command: str) -> None:
    run(capsys, "save", "-p", str(project.root), "-s", "state", "-a", "codex")
    data = json.loads(run(capsys, command, "-p", str(project.root), "--json")[1])
    assert data["project"]["key"] == project.key


def test_list_json(capsys, project: Project) -> None:
    run(capsys, "save", "-p", str(project.root), "-s", "state", "-a", "codex")
    data = json.loads(run(capsys, "list", "--json")[1])
    assert data[0]["last_agent"] == "codex"


def test_french_cli(capsys, project: Project, monkeypatch) -> None:
    monkeypatch.setenv("HANDOFF_LANG", "fr")
    i18n.set_language(None)
    code, out, _ = run(capsys, "save", "-p", str(project.root), "-s", "état")
    assert code == 0
    assert "Passation #1 enregistrée" in out
    code, _, err = run(capsys, "save", "-p", str(project.root), "-s", " ")
    assert "erreur" in err


def test_help_is_translated(capsys, monkeypatch) -> None:
    monkeypatch.setenv("HANDOFF_LANG", "fr")
    i18n.set_language(None)
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "utilisation" in out
    assert "historique des passations en graphe" in out


def test_broken_pipe_is_not_an_error(capsys, monkeypatch) -> None:
    def closed_pipe(*_args, **_kwargs):
        raise BrokenPipeError

    monkeypatch.setattr("ai_handoff.cli.cmd_where", closed_pipe)
    assert main(["where"]) == 0


def test_console_reports_broken_pipe_instead_of_exiting() -> None:
    from ai_handoff import ui

    with pytest.raises(BrokenPipeError):
        ui.console().on_broken_pipe()


def test_where_explains_a_folder_without_remote(capsys, project: Project) -> None:
    code, out, _ = run(capsys, "where", "-p", str(project.root))
    assert code == 0
    assert "not a git repository: the key is the folder path" in out
    assert "If you move or rename this folder" in out


def test_where_explains_a_remote_and_a_subfolder(capsys, make_repo) -> None:
    root = make_repo("app", remote="https://user:token@github.com/acme/app.git")
    (root / "src").mkdir()
    code, out, _ = run(capsys, "where", "-p", str(root / "src"))
    assert code == 0
    assert "git remote “origin”: https://github.com/acme/app.git" in out
    assert "token" not in out
    assert "You are in a subfolder (src)" in out
    data = json.loads(run(capsys, "where", "-p", str(root / "src"), "--json")[1])
    assert data["keyed_by"] == "remote"
    assert data["subfolder"] == "src"
    assert data["project"]["key"] == "git:github.com/acme/app"


def test_where_outside_a_project(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    code, out, _ = run(capsys, "where", "-p", str(tmp_path))
    assert code == 0
    assert "no project here" in out


@pytest.mark.parametrize(("language", "prefix"), [("en", "usage: "), ("fr", "utilisation : ")])
def test_subcommand_help_has_one_usage_prefix(capsys, monkeypatch, language: str, prefix: str) -> None:
    monkeypatch.setenv("HANDOFF_LANG", language)
    i18n.set_language(None)
    with pytest.raises(SystemExit):
        main(["log", "--help"])
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.startswith(f"{prefix}handoff log ")
    assert first_line.count(prefix.strip()) == 1
