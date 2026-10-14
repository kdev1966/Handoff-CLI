"""Stale and diverged handoffs: how far the code moved, and handoffs a session never saw."""

import json
from pathlib import Path

import pytest
from conftest import git
from mcp import Client
from mcp_types import TextContent

from ai_handoff import hooks, server, transfer
from ai_handoff.cli import main
from ai_handoff.context_file import context_path
from ai_handoff.project import Project, git_drift, resolve_project
from ai_handoff.server import mcp
from ai_handoff.store import Store


def commit(root: Path, name: str) -> None:
    (root / name).write_text(name, encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)


@pytest.fixture
def repo(make_repo) -> Path:
    root = make_repo("app")
    commit(root, "init.txt")
    return root


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test is a new MCP session: it has seen nothing yet."""
    monkeypatch.setattr(server, "_seen", {})


def save(project: Project, summary: str, agent: str = "codex", based_on: int | None = None) -> int:
    """A save by another session, straight into the store."""
    with Store() as store:
        return store.save(
            project, summary=summary, next_actions="Carry on", agent=agent, based_on=based_on
        ).id


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def save_args(project: Project, summary: str, **extra: object) -> dict[str, object]:
    return {"project_path": str(project.root), "summary": summary, "next_actions": "Carry on", **extra}


# Drift


def test_drift_counts_the_commits_since_the_handoff(repo: Path) -> None:
    project = resolve_project(repo)
    save(project, "Written at the first commit")
    with Store() as store:
        written_at = store.latest(project.key).git_commit
    assert git_drift(repo, written_at) is None
    commit(repo, "a.txt")
    commit(repo, "b.txt")
    drift = git_drift(repo, written_at)
    assert (drift.kind, drift.commits) == ("ahead", 2)
    assert drift.now.startswith(("main @ ", "master @ "))


def test_drift_on_another_branch_and_unknown_commit(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "feature.txt")
    project = resolve_project(repo)
    save(project, "Written on the feature branch")
    with Store() as store:
        written_at = store.latest(project.key).git_commit
    git(repo, "checkout", "-q", "-")
    assert git_drift(repo, written_at).kind == "behind"  # main is an older commit of that history
    commit(repo, "main-only.txt")
    assert git_drift(repo, written_at).kind == "elsewhere"
    assert git_drift(repo, "0" * 40).kind == "missing"
    assert git_drift(repo, None) is None


def test_drift_says_nothing_when_git_cannot_answer(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = resolve_project(repo)
    save(project, "Written at the first commit")
    with Store() as store:
        written_at = store.latest(project.key).git_commit
    commit(repo, "a.txt")
    monkeypatch.setattr("ai_handoff.project.git_exit_code", lambda *_args: None)  # timeout
    assert git_drift(repo, written_at) is None


@pytest.mark.anyio
async def test_memory_get_tells_how_far_the_code_moved(repo: Path) -> None:
    project = resolve_project(repo)
    save(project, "Auth module done, tests pending")
    commit(repo, "tests.txt")
    async with Client(mcp) as client:
        memory = text_of(await client.call_tool("memory_get", {"project_path": str(repo)}))
    assert "The code moved since: 1 commit on top of" in memory


# Divergence in the store


def test_intervening_handoffs_are_the_ones_the_author_did_not_see(project: Project) -> None:
    first = save(project, "Session start state")
    other = save(project, "Saved by another session", agent="copilot", based_on=first)
    late = save(project, "Saved without seeing the other", based_on=first)
    with Store() as store:
        latest = store.latest(project.key)
        assert latest.id == late
        assert [h.id for h in store.intervening(latest)] == [other]
        assert store.diverged(project.key) == {late: first}
        assert store.last_id(project.key) == late
        assert store.is_base(project.key, 0)
        assert store.is_base(project.key, first)
        assert not store.is_base(project.key, late + 1)


def test_an_imported_older_note_is_not_saved_in_between(project: Project) -> None:
    first = save(project, "Session start state")
    with Store() as store:
        store.import_handoff(
            project,
            agent="copilot",
            stack="",
            summary="Old note from another machine",
            next_actions="",
            redactions=0,
            created_at="2020-01-01T00:00:00+00:00",
            git_branch=None,
            git_commit=None,
        )
    late = save(project, "Saved after the import", based_on=first)
    with Store() as store:
        assert store.intervening(store.get_handoff(late)) == []
        assert store.diverged(project.key) == {}


def test_no_base_means_no_divergence(project: Project) -> None:
    save(project, "First state of the work")
    save(project, "Second state of the work")
    with Store() as store:
        assert store.intervening(store.latest(project.key)) == []
        assert store.diverged(project.key) == {}


# Divergence through the MCP server


@pytest.mark.anyio
async def test_save_after_another_session_is_flagged(project: Project) -> None:
    first = save(project, "Starting point of both sessions")
    async with Client(mcp) as client:
        await client.call_tool("memory_get", {"project_path": str(project.root)})
        other = save(project, "Billing done by the other session", agent="copilot", based_on=first)
        saved = text_of(await client.call_tool("memory_save", save_args(project, "Auth done, tests pending")))
        assert (
            f"Diverged: this handoff is based on #{first}, but #{other} by copilot was saved in between"
            in saved
        )
        assert "memory_history" in saved

        memory = text_of(await client.call_tool("memory_get", {"project_path": str(project.root)}))
    assert "Diverged:" in memory
    assert "Saved in between, not reflected in the handoff above" in memory
    assert memory.index("Auth done") < memory.index("Billing done by the other session")
    assert "Diverged:" in context_path(project).read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_consecutive_saves_of_one_session_do_not_diverge(project: Project) -> None:
    async with Client(mcp) as client:
        await client.call_tool("memory_get", {"project_path": str(project.root)})
        first = text_of(await client.call_tool("memory_save", save_args(project, "Auth module done")))
        # The context given at session start is stale now; the server knows better.
        second = text_of(
            await client.call_tool("memory_save", save_args(project, "Auth tests written", based_on=0))
        )
    assert "Diverged" not in first
    assert "Diverged" not in second


@pytest.mark.anyio
async def test_based_on_from_the_session_start_context(project: Project) -> None:
    first = save(project, "Starting point given by the hook")
    save(project, "Saved by another session meanwhile", agent="gemini-cli", based_on=first)
    async with Client(mcp) as client:  # never called memory_get
        saved = text_of(
            await client.call_tool("memory_save", save_args(project, "Work of this session", based_on=first))
        )
    assert "by gemini-cli was saved in between" in saved


@pytest.mark.anyio
async def test_unknown_based_on_is_ignored_with_a_warning(project: Project) -> None:
    async with Client(mcp) as client:
        saved = text_of(await client.call_tool("memory_save", save_args(project, "Work state", based_on=99)))
    assert "based_on #99 is not a handoff of this project" in saved
    with Store() as store:
        assert store.latest(project.key).based_on is None


@pytest.mark.anyio
async def test_three_overlapping_sessions_show_every_intervening_save(project: Project) -> None:
    start = save(project, "Common starting point")
    b = save(project, "Session B: billing done", agent="copilot", based_on=start)
    c = save(project, "Session C: docs updated", agent="gemini-cli", based_on=start)
    async with Client(mcp) as client:
        saved = text_of(
            await client.call_tool("memory_save", save_args(project, "Session A: auth done", based_on=start))
        )
        memory = text_of(await client.call_tool("memory_get", {"project_path": str(project.root)}))
    assert f"#{b} by copilot, #{c} by gemini-cli were saved in between" in saved
    assert "Session B: billing done" in memory
    assert "Session C: docs updated" in memory


def test_only_three_intervening_handoffs_are_shown_in_full(project: Project) -> None:
    from ai_handoff.briefing import latest_text

    start = save(project, "Common starting point")
    for number in range(5):
        save(project, f"Parallel session number {number}", based_on=start)
    save(project, "Late session state", based_on=start)
    with Store() as store:
        text = latest_text(store, project)
    assert "Parallel session number 4" in text
    assert "Parallel session number 2" in text
    assert "Parallel session number 1" not in text
    assert "2 more handoffs were saved in between: call memory_history" in text


# Session start hook


def test_session_start_gives_the_number_to_pass_back(repo: Path) -> None:
    project = resolve_project(repo)
    first = save(project, "Starting point of the work")
    output = json.loads(hooks.run("claude", "session-start", json.dumps({"cwd": str(repo)})))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert f"pass based_on={first}" in context
    assert context.index("based_on") < context.index("Starting point of the work")


def test_session_start_shows_divergence(repo: Path) -> None:
    project = resolve_project(repo)
    first = save(project, "Starting point of the work")
    save(project, "Saved by another session", agent="copilot", based_on=first)
    save(project, "Saved without seeing it", based_on=first)
    output = json.loads(hooks.run("claude", "session-start", json.dumps({"cwd": str(repo)})))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "Diverged:" in context
    assert "Saved by another session" in context


# Terminal views


def test_show_and_log_point_out_divergence(capsys, project: Project) -> None:
    first = save(project, "Starting point of the work")
    other = save(project, "Saved by another session", agent="copilot", based_on=first)
    late = save(project, "Saved without seeing it", based_on=first)

    assert main(["show", "-p", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert "Diverged:" in out
    assert f"handoff diff {other} {late}" in out

    assert main(["log", "-p", str(project.root)]) == 0
    assert f"diverged from #{first}:" in capsys.readouterr().out

    assert main(["show", "-p", str(project.root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["handoff"]["based_on"] == first
    assert data["intervening"] == [other]


def test_show_in_french(capsys, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_handoff import i18n

    first = save(project, "Starting point of the work")
    save(project, "Saved by another session", agent="copilot", based_on=first)
    save(project, "Saved without seeing it", based_on=first)
    monkeypatch.setenv("HANDOFF_LANG", "fr")
    i18n.set_language(None)
    main(["show", "-p", str(project.root)])
    assert "Divergence : cette passation part de" in capsys.readouterr().out


# Export and import


def test_import_keeps_links_with_new_numbers(project: Project, tmp_path: Path, monkeypatch) -> None:
    first = save(project, "Starting point of the work")
    save(project, "Saved by another session", agent="copilot", based_on=first)
    save(project, "Saved without seeing it", based_on=first)
    with Store() as store:
        lines = list(transfer.export_lines(store, [project.key]))

    monkeypatch.setenv("HANDOFF_HOME", str(tmp_path / "other-machine"))
    with Store() as store:
        # This machine already has a handoff of its own: imported numbers shift.
        store.save(resolve_project(tmp_path), summary="Unrelated project here", next_actions="")
        report = transfer.import_lines(lines, store, override=None, dry_run=False)
        assert report.imported == 3
        handoffs = store.all_handoffs(project.key)
        latest = store.latest(project.key)
        assert latest.based_on == handoffs[0].id != first
        assert [h.summary for h in store.intervening(latest)] == ["Saved by another session"]


def test_import_drops_links_it_cannot_resolve() -> None:
    def line(number: int, summary: str, day: int, based_on: int | None) -> str:
        return json.dumps(
            {
                "project": {"key": "git:github.com/acme/api", "name": "api", "root": "/elsewhere/api"},
                "id": number,
                "based_on": based_on,
                "agent": "codex",
                "summary": summary,
                "next_actions": "",
                "created_at": f"2026-09-0{day}T10:00:00+00:00",
            }
        )

    header = json.dumps({"format": transfer.FORMAT, "version": transfer.FORMAT_VERSION})
    lines = [
        header,
        line(7, "Written from an empty memory", 1, 0),
        line(8, "Based on a handoff missing from the file", 2, 3),
    ]
    with Store() as store:
        transfer.import_lines(lines, store, override=None, dry_run=False)
        first, second = store.all_handoffs("git:github.com/acme/api")
    assert first.based_on == 0
    assert second.based_on is None
