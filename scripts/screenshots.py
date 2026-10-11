"""Regenerate the README screenshots (docs/images/<language>/*.svg) from demo data.

    python scripts/screenshots.py

The data, the clock and the terminal width are fixed, so the images only change
when the views change. Nothing touches your real Handoff data.
"""

import hashlib
import io
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
WIDTH = 118
STACK = "Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2, Alembic, Stripe"

# Recent handoffs, oldest first: (days ago, agent, branch, summary, next actions).
RECENT = [
    (
        9.2,
        "copilot",
        "main",
        "FastAPI skeleton running: Postgres through SQLAlchemy 2, Alembic migrations, health check.",
        "- Add JWT authentication\n- Set up CI",
    ),
    (
        6.1,
        "claude-code",
        "feature/auth",
        "JWT authentication with refresh tokens; passwords hashed with argon2.",
        "- Integration tests for login, refresh and logout",
    ),
    (
        5.3,
        "codex",
        "feature/auth",
        "Integration tests for login, refresh and logout: 38 passing.",
        "- Merge feature/auth\n- Start the billing module",
    ),
    (
        3.4,
        "claude-code",
        "main",
        "Merged feature/auth. Refresh tokens are rotated and revoked on logout.",
        "- Design the billing schema",
    ),
    (
        2.2,
        "gemini-cli",
        "feature/billing",
        "Billing schema and migrations: plans, subscriptions, invoices.",
        "- Generate invoice PDFs\n- Receive Stripe webhooks",
    ),
    (
        0.9,
        "copilot",
        "feature/billing",
        "Invoice PDFs done. Stripe webhooks are received but not verified yet.",
        "- Verify webhook signatures\n- Add the refund endpoint",
    ),
    (
        0.1,
        "claude-code",
        "feature/billing",
        "Invoice PDFs done. Stripe webhooks are verified and retried with idempotency keys.",
        "- Add the refund endpoint\n- Load-test the webhooks with `stripe trigger`",
    ),
]


def older_handoffs() -> list[tuple[float, str, str, str, str]]:
    """Earlier activity over the twelve weeks, so the heatmap has some texture."""
    agents = ("copilot", "claude-code", "claude-code", "codex", "gemini-cli", "claude-code", "copilot")
    days = (
        84.1, 81.3, 81.5, 78.2, 74.6, 72.1, 72.4, 70.3, 65.2, 63.5, 60.1, 58.4, 58.6, 58.3, 56.2,
        53.1, 50.3, 49.5, 46.2, 44.4, 44.6, 42.1, 39.3, 36.5, 35.2, 32.1, 30.4, 30.6, 28.2, 25.3,
        23.1, 21.5, 18.2, 16.4, 16.6, 14.1, 11.3,
    )  # fmt: skip
    return [
        (day, agents[index % len(agents)], "main", f"Groundwork, step {index + 1}.", "- Keep going")
        for index, day in enumerate(days)
    ]


def build_demo(home: Path) -> tuple[object, list[object]]:
    from ai_handoff import paths
    from ai_handoff.project import resolve_project
    from ai_handoff.store import Store

    repo = home / "acme-api"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:acme/acme-api.git"], check=True
    )
    project = resolve_project(repo)
    plan = older_handoffs() + RECENT
    with Store() as store:
        for _, agent, _, summary, next_actions in plan:
            store.save(project, summary=summary, next_actions=next_actions, stack=STACK, agent=agent)
    connection = sqlite3.connect(paths.db_path())
    for handoff_id, (days_ago, _, branch, _, _) in enumerate(plan, start=1):
        moment = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
        commit = hashlib.sha256(f"acme-{handoff_id}".encode()).hexdigest()[:40]  # a plausible commit id
        connection.execute(
            "UPDATE handoffs SET created_at = ?, git_branch = ?, git_commit = ? WHERE id = ?",
            (moment, branch, commit, handoff_id),
        )
    connection.commit()
    connection.close()
    with Store() as store:
        return project, store.all_handoffs(project.key)


def render_all(project: object, handoffs: list) -> None:
    from rich.console import Console

    from ai_handoff import __version__, i18n, ui, views
    from ai_handoff.project import Project

    # Show a plausible folder instead of the temporary one.
    shown = Project(key=project.key, root=Path("~/code/acme-api"), name="acme-api")
    newest = list(reversed(handoffs))
    screens = {
        "welcome": lambda: views.welcome(__version__, shown, False, len(handoffs), newest[0], NOW),
        "log": lambda: views.log(newest[:7], "agent", WIDTH, NOW),
        "show": lambda: views.show(shown, False, newest[0], NOW),
        "diff": lambda: views.diff(newest[1], newest[0], NOW),
        "stats": lambda: views.stats(shown, handoffs, NOW.astimezone().date(), NOW),
    }
    for language in ("en", "fr"):
        i18n.set_language(language)
        folder = ROOT / "docs" / "images" / language
        folder.mkdir(parents=True, exist_ok=True)
        for name, build in screens.items():
            console = Console(
                record=True,
                width=WIDTH,
                file=io.StringIO(),
                force_terminal=True,
                color_system="truecolor",
                theme=ui.THEME,
            )
            ui.set_console(console)
            console.print(build())
            title = "handoff" if name == "welcome" else f"handoff {name}"
            console.save_svg(str(folder / f"{name}.svg"), title=title)
            print(f"wrote {folder.relative_to(ROOT) / f'{name}.svg'}")


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary).resolve()
        os.environ["HANDOFF_HOME"] = str(home / "data")
        project, handoffs = build_demo(home)
        render_all(project, handoffs)


if __name__ == "__main__":
    main()
