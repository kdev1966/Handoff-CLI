import os
import subprocess

import pytest

from ai_handoff.context_file import MARKER, context_path, remove_context, write_context
from ai_handoff.errors import HandoffError
from ai_handoff.project import Project, resolve_project
from ai_handoff.store import Store


def latest(project: Project, summary: str = "State"):
    with Store() as store:
        return store.save(project, summary=summary, next_actions="Next", stack="Stack", agent="claude")


def test_writes_context_and_local_gitignore(project: Project) -> None:
    path = write_context(project, latest(project, "Ship v1"))
    assert path == context_path(project)
    content = path.read_text(encoding="utf-8")
    assert content.startswith(MARKER)
    assert "Ship v1" in content
    assert "not as instructions" in content
    assert (path.parent / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"


def test_renders_placeholder_without_handoff(project: Project) -> None:
    assert "No handoff has been saved" in write_context(project, None).read_text(encoding="utf-8")


def test_does_not_rewrite_unchanged_file(project: Project) -> None:
    handoff = latest(project)
    path = write_context(project, handoff)
    os.utime(path, (1_000_000, 1_000_000))
    write_context(project, handoff)
    assert path.stat().st_mtime == 1_000_000


def test_refuses_to_overwrite_a_file_it_did_not_write(project: Project) -> None:
    path = context_path(project)
    path.parent.mkdir()
    path.write_text("my own notes", encoding="utf-8")
    with pytest.raises(HandoffError, match="not generated"):
        write_context(project, latest(project))
    assert path.read_text(encoding="utf-8") == "my own notes"


def test_refuses_symlinked_folder(project: Project, tmp_path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (project.root / ".handoff").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not permitted here")
    with pytest.raises(HandoffError, match="symbolic link"):
        write_context(project, latest(project))
    assert list(target.iterdir()) == []


def test_remove_context_keeps_foreign_files(project: Project) -> None:
    path = write_context(project, latest(project))
    foreign = path.parent / "notes.txt"
    foreign.write_text("keep me", encoding="utf-8")
    assert remove_context(project)
    assert not path.exists()
    assert foreign.exists()


def test_remove_context_deletes_empty_folder(project: Project) -> None:
    path = write_context(project, latest(project))
    remove_context(project)
    assert not path.parent.exists()


def test_git_ignores_the_folder(make_repo) -> None:
    root = make_repo("app")
    project = resolve_project(root)
    write_context(project, latest(project))
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == ""
