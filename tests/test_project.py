import os
import shutil
from pathlib import Path

import pytest

from ai_handoff.errors import ProjectError
from ai_handoff.project import normalize_remote, resolve_project


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/Owner/Repo.git", "github.com/owner/repo"),
        ("https://user:s3cret@github.com/owner/repo", "github.com/owner/repo"),
        ("git@github.com:owner/repo.git", "github.com/owner/repo"),
        ("ssh://git@gitlab.example.com:2222/group/sub/repo.git", "gitlab.example.com/group/sub/repo"),
        ("https://github.com/owner/repo/", "github.com/owner/repo"),
        ("file:///srv/git/repo.git", None),
        ("/srv/git/repo.git", None),
        ("../other-repo", None),
        ("C:\\repos\\project", None),
        ("https://github.com/", None),
    ],
)
def test_normalize_remote(url: str, expected: str | None) -> None:
    assert normalize_remote(url) == expected


def test_plain_folder_uses_its_path_as_key(tmp_path: Path) -> None:
    folder = tmp_path / "my-app"
    folder.mkdir()
    project = resolve_project(folder)
    assert project.root == folder.resolve()
    assert project.name == "my-app"
    assert project.key == f"path:{os.path.normcase(str(folder.resolve()))}"


def test_subfolder_resolves_to_repository_root(make_repo) -> None:
    root = make_repo("app", remote="git@github.com:me/app.git")
    sub = root / "src" / "pkg"
    sub.mkdir(parents=True)
    project = resolve_project(sub)
    assert project.root == root
    assert project.key == "git:github.com/me/app"


def test_repository_without_remote_uses_path(make_repo) -> None:
    root = make_repo("local-only")
    assert resolve_project(root).key == f"path:{os.path.normcase(str(root))}"


def test_key_survives_moving_the_repository(make_repo, tmp_path: Path) -> None:
    root = make_repo("app", remote="https://github.com/me/app.git")
    before = resolve_project(root).key
    moved = tmp_path / "elsewhere" / "renamed"
    moved.parent.mkdir()
    shutil.move(str(root), str(moved))
    assert resolve_project(moved).key == before


@pytest.mark.parametrize("bad", ["", "   ", "relative/path"])
def test_rejects_empty_and_relative_paths(bad: str) -> None:
    with pytest.raises(ProjectError):
        resolve_project(bad)


def test_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="does not exist"):
        resolve_project(tmp_path / "missing")


def test_rejects_a_file(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("x")
    with pytest.raises(ProjectError, match="not a directory"):
        resolve_project(file)


def test_rejects_filesystem_root() -> None:
    with pytest.raises(ProjectError, match="Refusing"):
        resolve_project(Path(Path.cwd().anchor))


def test_rejects_home_and_its_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "users" / "alice"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(ProjectError, match="Refusing"):
        resolve_project(home)
    with pytest.raises(ProjectError, match="Refusing"):
        resolve_project(home.parent)


def test_dotfiles_repository_in_home_does_not_capture_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = (tmp_path / "home").resolve()
    (home / ".git").mkdir(parents=True)
    project_dir = home / "code" / "app"
    project_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert resolve_project(project_dir).root == project_dir


def test_git_context_without_commits_or_repository(make_repo, tmp_path: Path) -> None:
    from ai_handoff.project import git_context

    assert git_context(make_repo("empty")) == (None, None)
    assert git_context(tmp_path) == (None, None)
