import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from ai_handoff import paths
from ai_handoff.errors import HandoffError, NotFoundError, PausedError, ValidationError
from ai_handoff.project import Project
from ai_handoff.store import MAX_FIELD_BYTES, SCHEMA_VERSION, Store


def save(store: Store, project: Project, **overrides: object):
    fields = {"summary": "Login page done", "next_actions": "Add tests", "stack": "Python", "agent": "claude"}
    fields.update(overrides)
    return store.save(project, **fields)


def test_save_and_read_latest(project: Project) -> None:
    with Store() as store:
        assert store.latest(project.key) is None
        first = save(store, project)
        second = save(store, project, summary="API done", agent="copilot")
        latest = store.latest(project.key)
    assert latest == second
    assert latest.id > first.id
    assert (latest.summary, latest.agent, latest.stack) == ("API done", "copilot", "Python")


def test_history_is_append_only_and_newest_first(project: Project) -> None:
    with Store() as store:
        for index in range(3):
            save(store, project, summary=f"step {index}")
        history = store.history(project.key, limit=10)
    assert [handoff.summary for handoff in history] == ["step 2", "step 1", "step 0"]


def test_omitted_stack_keeps_previous_one(project: Project) -> None:
    with Store() as store:
        save(store, project, stack="FastAPI + Postgres")
        handoff = save(store, project, stack=None)
    assert handoff.stack == "FastAPI + Postgres"


def test_projects_are_isolated(tmp_path: Path, project: Project) -> None:
    other = Project(key="path:other", root=tmp_path, name="other")
    with Store() as store:
        save(store, project, summary="mine")
        save(store, other, summary="theirs")
        assert store.latest(project.key).summary == "mine"
        assert store.latest(other.key).summary == "theirs"


@pytest.mark.parametrize("summary", ["", "   \n\t "])
def test_summary_is_required(project: Project, summary: str) -> None:
    with Store() as store, pytest.raises(ValidationError, match="summary"):
        save(store, project, summary=summary)


def test_rejects_oversized_fields(project: Project) -> None:
    with Store() as store, pytest.raises(ValidationError, match="limit"):
        save(store, project, next_actions="é" * MAX_FIELD_BYTES)


def test_rejects_invalid_unicode(project: Project) -> None:
    with Store() as store, pytest.raises(ValidationError, match="Unicode"):
        save(store, project, summary="broken \ud800 surrogate")


@pytest.mark.parametrize("agent", ["x" * 65, "evil\nagent", "<script>"])
def test_rejects_invalid_agent_names(project: Project, agent: str) -> None:
    with Store() as store, pytest.raises(ValidationError, match="agent"):
        save(store, project, agent=agent)


def test_normalizes_text(project: Project) -> None:
    with Store() as store:
        handoff = save(store, project, summary="  line1\r\nline2\x00\x1b[31m\tend  ", agent="")
    assert handoff.summary == "line1\nline2[31m\tend"
    assert handoff.agent == "unknown"


def test_masks_secrets_and_counts_them(project: Project) -> None:
    with Store() as store:
        handoff = save(store, project, summary="token ghp_" + "a" * 36, stack="password=hunter22")
    assert "ghp_" not in handoff.summary
    assert "hunter22" not in handoff.stack
    assert handoff.redactions == 2


def test_pause_blocks_saving(project: Project) -> None:
    with Store() as store:
        store.set_paused(project, True)
        assert store.is_paused(project.key)
        with pytest.raises(PausedError):
            save(store, project)
        store.set_paused(project, False)
        save(store, project)


def test_restore_appends_a_copy(project: Project) -> None:
    with Store() as store:
        old = save(store, project, summary="good state")
        save(store, project, summary="bad state")
        restored = store.restore(old.id, project)
        assert store.latest(project.key) == restored
        assert len(store.history(project.key)) == 3
    assert restored.summary == "good state"
    assert restored.agent == f"restore:{old.id}"


def test_restore_refuses_another_projects_handoff(tmp_path: Path, project: Project) -> None:
    other = Project(key="path:other", root=tmp_path, name="other")
    with Store() as store:
        foreign = save(store, other)
        with pytest.raises(NotFoundError):
            store.restore(foreign.id, project)


def test_purge_deletes_project_and_history(project: Project) -> None:
    with Store() as store:
        save(store, project)
        save(store, project)
        assert store.purge(project.key) == 2
        assert store.get_project(project.key) is None
        assert store.history(project.key) == []
        with pytest.raises(NotFoundError):
            store.purge(project.key)


def test_list_projects(tmp_path: Path, project: Project) -> None:
    other = Project(key="path:other", root=tmp_path, name="other")
    with Store() as store:
        save(store, project)
        save(store, project)
        store.set_paused(other, True)
        records = {record.key: record for record in store.list_projects()}
    assert records[project.key].handoffs == 2
    assert records[other.key].paused
    assert records[other.key].last_handoff_at is None


def test_moved_project_updates_its_root(tmp_path: Path, project: Project) -> None:
    moved = Project(key=project.key, root=tmp_path / "new-place", name="new-name")
    with Store() as store:
        save(store, project)
        save(store, moved)
        record = store.get_project(project.key)
    assert (record.name, record.root) == ("new-name", str(moved.root))


def test_concurrent_stores_see_each_other(project: Project) -> None:
    with Store() as first, Store() as second:
        save(first, project, summary="from first")
        save(second, project, summary="from second")
        assert first.latest(project.key).summary == "from second"


def test_refuses_database_from_newer_version(handoff_home: Path) -> None:
    handoff_home.mkdir(parents=True)
    conn = sqlite3.connect(paths.db_path())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(HandoffError, match="newer version"):
        Store()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_database_is_private(handoff_home: Path) -> None:
    Store().close()
    assert stat.S_IMODE(os.stat(handoff_home).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(paths.db_path()).st_mode) == 0o600


def test_migrates_a_version_1_database(handoff_home: Path, project: Project) -> None:
    from ai_handoff.store import MIGRATIONS

    handoff_home.mkdir(parents=True)
    conn = sqlite3.connect(paths.db_path())
    conn.executescript(MIGRATIONS[1] + "PRAGMA user_version = 1;")
    conn.execute(
        "INSERT INTO projects (key, name, root, created_at) VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')",
        (project.key, project.name, str(project.root)),
    )
    conn.execute(
        "INSERT INTO handoffs (project_key, agent, stack, summary, next_actions, created_at) "
        "VALUES (?, 'old', '', 'from v1', '', '2026-01-01T00:00:00+00:00')",
        (project.key,),
    )
    conn.commit()
    conn.close()
    with Store() as store:
        old = store.latest(project.key)
        assert (old.summary, old.git_branch, old.git_commit) == ("from v1", None, None)
        save(store, project)
    conn = sqlite3.connect(paths.db_path())
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_records_git_branch_and_commit(make_repo) -> None:
    from conftest import git

    from ai_handoff.project import resolve_project

    root = make_repo("app")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    git(root, "checkout", "-q", "-b", "feature/graphs")
    project = resolve_project(root)
    with Store() as store:
        handoff = save(store, project)
    assert handoff.git_branch == "feature/graphs"
    assert handoff.git_commit and len(handoff.git_commit) == 40
    assert handoff.git_label().startswith("feature/graphs @ ")


def test_all_handoffs_and_since(project: Project) -> None:
    with Store() as store:
        save(store, project, summary="a")
        save(store, project, summary="b")
        assert [h.summary for h in store.all_handoffs(project.key)] == ["a", "b"]
        assert len(store.handoffs_since("2000-01-01T00:00:00+00:00")) == 2
        assert store.handoffs_since("2999-01-01T00:00:00+00:00") == []
        assert store.list_projects()[0].last_agent == "claude-code"


def test_agent_aliases_are_merged(project: Project) -> None:
    with Store() as store:
        handoff = save(store, project, agent="Claude")
        assert handoff.agent == "claude-code"
        assert save(store, project, agent="gemini").agent == "gemini-cli"
        assert save(store, project, agent="my-own-bot").agent == "my-own-bot"


def test_old_aliases_are_shown_under_the_tool_name(handoff_home: Path, project: Project) -> None:
    with Store() as store:
        save(store, project, agent="claude-code")
    conn = sqlite3.connect(paths.db_path())
    conn.execute("UPDATE handoffs SET agent = 'claude'")
    conn.commit()
    conn.close()
    with Store() as store:
        assert store.latest(project.key).agent == "claude-code"
        assert store.list_projects()[0].last_agent == "claude-code"


def test_manual_save_keeps_the_assistants_baseline(project: Project) -> None:
    with Store() as store:
        store.set_fingerprints(project.key, baseline="assistant-start", reminded=None)
        save(store, project, agent="cli")
        assert store.fingerprints(project.key) == ("assistant-start", None)
        save(store, project, agent="codex")
        assert store.fingerprints(project.key)[0] != "assistant-start"


def test_restore_keeps_the_assistants_baseline(project: Project) -> None:
    with Store() as store:
        first = save(store, project, agent="codex")
        store.set_fingerprints(project.key, baseline="assistant-start", reminded=None)
        store.restore(first.id, project)
        assert store.fingerprints(project.key) == ("assistant-start", None)


@pytest.mark.parametrize(
    ("summary", "next_actions", "expected"),
    [
        ("SUMMARY", "Write tests", ["warning.summary_short"]),
        ("Auth module done, tests pending.", "", ["warning.no_next_actions"]),
        ("todo", "", ["warning.summary_short", "warning.no_next_actions"]),
        ("Auth module done, tests pending.", "Write the integration tests", []),
    ],
)
def test_warnings(project: Project, summary: str, next_actions: str, expected: list[str]) -> None:
    with Store() as store:
        assert save(store, project, summary=summary, next_actions=next_actions).warnings() == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Copilot", "copilot"), ("CODEX", "codex"), ("Claude", "claude-code"), ("MyBot", "MyBot")],
)
def test_known_agent_names_ignore_case(project: Project, name: str, expected: str) -> None:
    with Store() as store:
        assert save(store, project, agent=name).agent == expected
