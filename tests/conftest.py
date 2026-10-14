import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ai_handoff import i18n, ui
from ai_handoff.project import Project, resolve_project


@pytest.fixture(autouse=True)
def handoff_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own empty database, and never reaches the network."""
    home = tmp_path / "handoff-data"
    monkeypatch.setenv("HANDOFF_HOME", str(home))
    monkeypatch.setenv("HANDOFF_NO_UPDATE_CHECK", "1")
    return home


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch):
    """Tests run in English with plain output unless they say otherwise."""
    monkeypatch.setenv("HANDOFF_LANG", "en")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    i18n.set_language(None)
    ui.reset()
    yield
    i18n.set_language(None)
    ui.reset()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[..., Path]:
    """Create a git repository, optionally with an 'origin' remote."""
    if not shutil.which("git"):
        pytest.skip("git is not installed")

    def _make(name: str = "repo", remote: str | None = None) -> Path:
        root = tmp_path / "work" / name
        root.mkdir(parents=True)
        git(root, "init", "-q")
        if remote:
            git(root, "remote", "add", "origin", remote)
        return root.resolve()

    return _make


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A plain folder project without git."""
    root = tmp_path / "work" / "plain-project"
    root.mkdir(parents=True)
    return resolve_project(root)
