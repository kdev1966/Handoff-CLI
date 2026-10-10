import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_handoff import completion
from ai_handoff.cli import build_parser, main
from ai_handoff.integrations.files import Session
from ai_handoff.project import resolve_project
from ai_handoff.store import Store


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return home


def test_commands_come_from_the_parser() -> None:
    names = [command.name for command in completion.commands(build_parser())]
    assert {"show", "log", "diff", "stats", "list", "setup", "doctor", "completion"} <= set(names)
    assert "hook" not in names
    assert "serve" not in names
    log = next(command for command in completion.commands(build_parser()) if command.name == "log")
    by = next(option for option in log.options if "--by" in option.flags)
    assert by.choices == ("agent", "branch")
    assert any(option.is_path for option in log.options)


@pytest.mark.parametrize("shell", completion.SHELLS)
def test_scripts_mention_every_command(shell: str) -> None:
    text = completion.script(shell, build_parser())
    for name in ("show", "log", "stats", "completion"):
        assert name in text


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_scripts_are_valid_shell(shell: str, tmp_path: Path) -> None:
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    path = tmp_path / f"handoff.{shell}"
    path.write_text(completion.script(shell, build_parser()), encoding="utf-8")
    assert subprocess.run([shell, "-n", str(path)], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")
def test_bash_completion_behaviour(tmp_path: Path) -> None:
    script = tmp_path / "handoff.bash"
    script.write_text(completion.script("bash", build_parser()), encoding="utf-8")
    driver = tmp_path / "driver.bash"
    driver.write_text(
        f"""source "{script}"
try() {{ COMP_WORDS=("$@"); COMP_CWORD=$((${{#COMP_WORDS[@]}} - 1)); _handoff; echo "${{COMPREPLY[*]}}"; }}
try handoff l
try handoff log --by ""
try handoff completion ""
""",
        encoding="utf-8",
    )
    lines = subprocess.run(
        ["bash", str(driver)], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert lines[0] == "log list"
    assert lines[1] == "agent branch"
    assert lines[2].startswith("zsh bash powershell")


def test_install_is_idempotent_and_reversible(fake_home: Path) -> None:
    profile = fake_home / ".zshrc"
    profile.write_text("export EDITOR=vim\n", encoding="utf-8")
    parser = build_parser()
    completion.install("zsh", parser, Session())
    completion.install("zsh", parser, Session())
    text = profile.read_text(encoding="utf-8")
    assert text.startswith("export EDITOR=vim\n")
    assert text.count(completion.PROFILE_MARKER) == 1
    assert completion.script_path("zsh").exists()
    assert completion.installed("zsh")

    completion.script_path("zsh").write_text("old", encoding="utf-8")
    completion.refresh(parser)
    assert "compdef _handoff handoff" in completion.script_path("zsh").read_text(encoding="utf-8")

    completion.uninstall(Session())
    assert profile.read_text(encoding="utf-8") == "export EDITOR=vim\n"
    assert not completion.script_path("zsh").exists()


def test_profile_line_quotes_paths_with_spaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HANDOFF_HOME", str(tmp_path / "Application Support" / "handoff"))
    line = completion.profile_line("zsh")
    assert '"' + str(tmp_path / "Application Support") in line
    assert completion.PROFILE_MARKER in line


def test_completion_command(capsys, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["completion", "bash"]) == 0
    assert "complete -F _handoff handoff" in capsys.readouterr().out
    monkeypatch.setattr(completion, "detect_shell", lambda: "zsh")
    assert main(["completion", "--install"]) == 0
    assert "Tab completion installed for zsh" in capsys.readouterr().out
    assert main(["completion", "--uninstall"]) == 0
    assert "Tab completion removed" in capsys.readouterr().out


def test_completion_needs_a_known_shell(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(completion, "detect_shell", lambda: None)
    assert main(["completion"]) == 1
    assert "Unknown shell" in capsys.readouterr().err


def test_welcome_outside_a_project(capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # cwd is home: no project
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "No project here" in out
    assert "handoff log" in out
    assert "handoff hook" not in out


def test_welcome_in_a_project(capsys, project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project.root)
    with Store() as store:
        store.save(resolve_project(project.root), summary="state", next_actions="", agent="copilot")
    assert main([]) == 0
    out = capsys.readouterr().out
    assert project.name in out
    assert "1 handoff" in out
    assert "copilot" in out
