import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from ai_handoff import transfer
from ai_handoff.cli import main
from ai_handoff.errors import HandoffError
from ai_handoff.project import Project
from ai_handoff.store import Store


def run(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def header(**overrides: object) -> str:
    return json.dumps({"format": transfer.FORMAT, "version": transfer.FORMAT_VERSION, **overrides})


def entry(**overrides: object) -> str:
    data: dict[str, object] = {
        "project": {"key": "git:github.com/acme/api", "name": "api", "root": "/elsewhere/api"},
        "agent": "copilot",
        "stack": "Python",
        "summary": "Billing schema done, migrations applied.",
        "next_actions": "- Invoice PDFs",
        "created_at": "2026-09-01T10:00:00+00:00",
        "git_branch": "main",
        "git_commit": "a1b2c3d4e5f6",
        "redactions": 0,
    }
    data.update(overrides)
    return json.dumps(data)


def test_export_then_import_on_another_machine(capsys, project: Project, tmp_path: Path, monkeypatch) -> None:
    path = str(project.root)
    run(capsys, "save", "-p", path, "-s", "First state of the project", "-n", "Next", "-a", "codex")
    run(capsys, "save", "-p", path, "-s", "Second state of the project", "-n", "Next", "-a", "copilot")
    export = tmp_path / "memory.jsonl"
    assert run(capsys, "export", "-p", path, "-o", str(export))[0] == 0
    lines = export.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["format"] == transfer.FORMAT
    assert len(lines) == 3
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(export).st_mode) == 0o600

    monkeypatch.setenv("HANDOFF_HOME", str(tmp_path / "other-machine"))
    code, out, _ = run(capsys, "import", str(export))
    assert code == 0
    assert "Imported: 2" in out
    with Store() as store:
        handoffs = store.all_handoffs(project.key)
    assert [h.agent for h in handoffs] == ["codex", "copilot"]

    code, out, _ = run(capsys, "import", str(export))
    assert "Imported: 0 · already present: 2" in out


def test_export_to_stdout_and_all(capsys, project: Project) -> None:
    run(capsys, "save", "-p", str(project.root), "-s", "A state worth exporting", "-n", "x")
    code, out, _ = run(capsys, "export", "--all")
    assert code == 0
    assert len(out.splitlines()) == 2


def test_export_needs_a_memory(capsys, project: Project) -> None:
    code, _, err = run(capsys, "export", "-p", str(project.root))
    assert code == 1
    assert "no memory to export" in err


def test_imported_content_is_validated_and_masked() -> None:
    lines = [
        header(),
        entry(summary="Deployed with token ghp_" + "a" * 36, agent="Claude"),
        entry(summary="   "),
        "not json",
        entry(created_at="yesterday"),
        entry(project={"key": "evil", "name": "x"}),
        entry(git_commit="not a commit; rm -rf /", summary="Another valid handoff here"),
    ]
    with Store() as store:
        report = transfer.import_lines(lines, store, override=None, dry_run=False)
        handoffs = store.all_handoffs("git:github.com/acme/api")
    assert report.imported == 2
    assert len(report.errors) == 4
    assert all(error.startswith("line ") for error in report.errors)
    assert "ghp_" not in handoffs[0].summary
    assert handoffs[0].redactions == 1
    assert handoffs[0].agent == "claude-code"
    assert handoffs[1].git_commit is None


def test_header_is_required_and_versioned() -> None:
    with Store() as store:
        with pytest.raises(HandoffError, match="not a Handoff export"):
            transfer.import_lines([entry()], store, override=None, dry_run=False)
        with pytest.raises(HandoffError, match="newer"):
            transfer.import_lines([header(version=99), entry()], store, override=None, dry_run=False)


def test_dry_run_writes_nothing() -> None:
    with Store() as store:
        report = transfer.import_lines([header(), entry()], store, override=None, dry_run=True)
        assert report.imported == 1
        assert store.all_handoffs("git:github.com/acme/api") == []


def test_imported_older_handoff_does_not_become_the_latest(project: Project) -> None:
    with Store() as store:
        store.save(project, summary="Today's work on the project", next_actions="x", agent="codex")
        transfer.import_lines(
            [header(), entry(project={"key": project.key, "name": project.name, "root": "/x"})],
            store,
            override=None,
            dry_run=False,
        )
        assert store.latest(project.key).summary == "Today's work on the project"
        assert store.get_project(project.key).root == str(project.root)  # the local folder wins


def test_path_attaches_a_project_without_remote(capsys, project: Project, tmp_path: Path) -> None:
    export = tmp_path / "one.jsonl"
    export.write_text(
        "\n".join([header(), entry(project={"key": "path:/old/machine/app", "name": "app"})]) + "\n",
        encoding="utf-8",
    )
    code, out, _ = run(capsys, "import", str(export))
    assert "No git remote for app" in out
    code, _, _ = run(capsys, "import", str(export), "--path", str(project.root))
    assert code == 0
    with Store() as store:
        assert len(store.all_handoffs(project.key)) == 1


def test_path_refuses_an_export_with_several_projects(capsys, project: Project, tmp_path: Path) -> None:
    export = tmp_path / "many.jsonl"
    export.write_text(
        "\n".join([header(), entry(), entry(project={"key": "git:github.com/acme/web", "name": "web"})]),
        encoding="utf-8",
    )
    code, _, err = run(capsys, "import", str(export), "--path", str(project.root))
    assert code == 1
    assert "2 projects" in err


def test_import_from_stdin(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(header() + "\n" + entry() + "\n"))
    code, out, _ = run(capsys, "import", "-")
    assert code == 0
    assert "Imported: 1" in out


def test_import_unreadable_file(capsys, tmp_path: Path) -> None:
    code, _, err = run(capsys, "import", str(tmp_path / "missing.jsonl"))
    assert code == 1
    assert "Cannot read" in err
