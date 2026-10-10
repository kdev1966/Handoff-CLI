"""Command-line interface: the same memory as the MCP server, plus views and administration."""

import argparse
import contextlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.text import Text

from ai_handoff import __version__, activity, paths, ui, views
from ai_handoff.context_file import context_path, remove_context, write_context
from ai_handoff.errors import HandoffError
from ai_handoff.i18n import t, tn
from ai_handoff.project import Project, resolve_project
from ai_handoff.store import MAX_HISTORY, Handoff, Store


def _project(path: str | None) -> Project:
    return resolve_project(Path(path).absolute() if path else Path.cwd())


def _read_value(value: str | None) -> str | None:
    """'-' reads the value from standard input, for multi-line text."""
    return sys.stdin.read() if value == "-" else value


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _project_json(project: Project) -> dict[str, str]:
    return {"key": project.key, "name": project.name, "root": str(project.root)}


def _say(message: str, style: str = "") -> None:
    ui.console().print(Text(message, style=style))


def cmd_serve(args: argparse.Namespace) -> int:
    from ai_handoff.server import serve

    serve()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        paused = store.is_paused(project.key)
        handoff = store.latest(project.key)
    if args.json:
        _print_json(
            {
                "project": _project_json(project),
                "paused": paused,
                "handoff": asdict(handoff) if handoff else None,
            }
        )
        return 0
    ui.console().print(views.show(project, paused, handoff))
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    if [args.summary, args.next, args.stack].count("-") > 1:
        raise HandoffError(t("error.stdin_once"))
    project = _project(args.path)
    with Store() as store:
        handoff = store.save(
            project,
            summary=_read_value(args.summary),
            next_actions=_read_value(args.next) or "",
            stack=_read_value(args.stack),
            agent=args.agent,
        )
    write_context(project, handoff)
    _say(t("save.done", id=handoff.id, project=project.name), "success")
    if handoff.redactions:
        _say(tn("show.redactions", handoff.redactions), "warning")
    return 0


def _history(args: argparse.Namespace) -> tuple[Project, list[Handoff]]:
    project = _project(args.path)
    with Store() as store:
        return project, store.history(project.key, limit=args.limit)


def cmd_history(args: argparse.Namespace) -> int:
    project, handoffs = _history(args)
    if args.json:
        _print_json({"project": _project_json(project), "handoffs": [asdict(h) for h in handoffs]})
    elif not handoffs:
        _say(t("show.no_handoff"), "muted")
    else:
        ui.console().print(views.history(handoffs))
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    project, handoffs = _history(args)
    if args.json:
        _print_json({"project": _project_json(project), "handoffs": [asdict(h) for h in handoffs]})
    elif not handoffs:
        _say(t("show.no_handoff"), "muted")
    else:
        console = ui.console()
        console.print(views.log(handoffs, args.by, console.width), soft_wrap=True)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        if args.new is not None:
            new = store.get_project_handoff(args.new, project)
        else:
            new = store.latest(project.key)
        if args.old is not None:
            old = store.get_project_handoff(args.old, project)
        else:
            recent = store.history(project.key, limit=2)
            old = recent[1] if len(recent) == 2 else None
    if new is None or old is None:
        raise HandoffError(t("error.diff_needs_two"))
    ui.console().print(views.diff(old, new))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        handoffs = store.all_handoffs(project.key)
    if args.json:
        today = datetime.now().astimezone().date()
        _print_json(
            {
                "project": _project_json(project),
                "handoffs": len(handoffs),
                "agents": dict(Counter(h.agent for h in handoffs if not h.is_restore)),
                "branches": dict(Counter(h.git_branch for h in handoffs if h.git_branch)),
                "restores": sum(h.is_restore for h in handoffs),
                "weekly": activity.weekly_counts(handoffs, views.STATS_WEEKS, today),
                "first": handoffs[0].created_at if handoffs else None,
                "last": handoffs[-1].created_at if handoffs else None,
            }
        )
        return 0
    ui.console().print(views.stats(project, handoffs, datetime.now().astimezone().date()))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        if store.is_paused(project.key):
            raise HandoffError(t("error.paused_render", project=project.name))
        handoff = store.latest(project.key)
    print(write_context(project, handoff))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    since = datetime.now(timezone.utc) - timedelta(weeks=views.LIST_WEEKS + 1)
    with Store() as store:
        records = store.list_projects()
        recent = store.handoffs_since(since.isoformat(timespec="seconds"))
    if args.json:
        _print_json([asdict(record) for record in records])
        return 0
    if not records:
        _say(t("list.empty"), "muted")
        return 0
    ui.console().print(views.projects(records, recent, datetime.now().astimezone().date()))
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        store.set_paused(project, True)
    remove_context(project)
    _say(t("pause.done", project=project.name), "warning")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        store.set_paused(project, False)
        handoff = store.latest(project.key)
    if handoff:
        write_context(project, handoff)
    _say(t("resume.done", project=project.name), "success")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    with Store() as store:
        if args.key:
            record = store.get_project(args.key)
            if not record:
                raise HandoffError(t("error.project_key_not_found", key=args.key))
            project = Project(key=record.key, root=Path(record.root), name=record.name)
        else:
            project = _project(args.path)
            if not store.get_project(project.key):
                raise HandoffError(t("error.nothing_to_purge", project=project.name))
        if not args.yes and not _confirm(t("purge.confirm", project=project.name, key=project.key)):
            _say(t("purge.cancelled"), "muted")
            return 1
        count = store.purge(project.key)
    if project.root.is_dir():
        remove_context(project)
    _say(t("purge.done", project=project.name) + " " + tn("count.deleted", count), "success")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        handoff = store.restore(args.id, project)
    write_context(project, handoff)
    _say(t("restore.done", old=args.id, new=handoff.id, project=project.name), "success")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from ai_handoff import setup_flow
    from ai_handoff.integrations import mcp_config_json

    if args.print_config:
        print(mcp_config_json())
        return 0
    interactive = sys.stdin.isatty()
    if args.remove:
        return setup_flow.run_remove(yes=args.yes, dry_run=args.dry_run, interactive=interactive)
    return setup_flow.run_setup(
        yes=args.yes,
        names=args.client,
        hooks=False if args.no_hooks else None,
        dry_run=args.dry_run,
        interactive=interactive,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    from ai_handoff import setup_flow

    ui.console().print(setup_flow.doctor())
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    from ai_handoff import hooks

    output = hooks.run(args.client, args.event, sys.stdin.read())
    if output:
        sys.stdout.write(output + "\n")
    return 0


def cmd_where(args: argparse.Namespace) -> int:
    _say(f"{t('where.database')} {paths.db_path()}")
    try:
        _say(f"{t('where.context')} {context_path(_project(args.path))}")
    except HandoffError as exc:
        _say(f"{t('where.context')} ({t('where.no_project', reason=exc)})", "muted")
    return 0


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} {t('confirm.choices')} ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes", "o", "oui"}


class _Formatter(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, prefix or t("help.usage"))


def _localize(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser._positionals.title = t("help.positionals")
    parser._optionals.title = t("help.options")
    parser.add_argument("-h", "--help", action="help", help=t("help.help"))
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = _localize(
        argparse.ArgumentParser(
            prog="handoff", description=t("help.description"), formatter_class=_Formatter, add_help=False
        )
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}", help=t("help.version")
    )
    sub = parser.add_subparsers(
        dest="command", required=True, metavar=t("help.command"), title=t("help.commands")
    )

    def add(name: str, func, *, path: bool = True, json: bool = False) -> argparse.ArgumentParser:
        text = t(f"cmd.{name}")
        command = _localize(
            sub.add_parser(name, help=text, description=text, formatter_class=_Formatter, add_help=False)
        )
        command.set_defaults(func=func)
        if path:
            command.add_argument("--path", "-p", help=t("opt.path"))
        if json:
            command.add_argument("--json", action="store_true", help=t("opt.json"))
        return command

    add("show", cmd_show, json=True)

    log = add("log", cmd_log, json=True)
    log.add_argument("--limit", "-l", type=int, default=20, choices=range(1, MAX_HISTORY + 1), metavar="N")
    log.add_argument("--by", choices=("agent", "branch"), default="agent", help=t("opt.by"))

    history = add("history", cmd_history, json=True)
    history.add_argument(
        "--limit", "-l", type=int, default=10, choices=range(1, MAX_HISTORY + 1), metavar="N"
    )

    diff = add("diff", cmd_diff)
    diff.add_argument("old", type=int, nargs="?", help=t("opt.diff_old"))
    diff.add_argument("new", type=int, nargs="?", help=t("opt.diff_new"))

    add("stats", cmd_stats, json=True)
    add("list", cmd_list, path=False, json=True)

    save = add("save", cmd_save)
    save.add_argument("--summary", "-s", required=True, help=t("opt.summary"))
    save.add_argument("--next", "-n", default="", help=t("opt.next"))
    save.add_argument("--stack", help=t("opt.stack"))
    save.add_argument("--agent", "-a", default="cli", help=t("opt.agent"))

    restore = add("restore", cmd_restore)
    restore.add_argument("id", type=int, help=t("opt.restore_id"))

    add("pause", cmd_pause)
    add("resume", cmd_resume)

    purge = add("purge", cmd_purge)
    purge.add_argument("--key", help=t("opt.key"))
    purge.add_argument("--yes", "-y", action="store_true", help=t("opt.yes"))

    add("render", cmd_render)

    setup = add("setup", cmd_setup, path=False)
    setup.add_argument("--yes", "-y", action="store_true", help=t("opt.setup_yes"))
    setup.add_argument("--client", action="append", metavar="NAME", help=t("opt.client"))
    setup.add_argument("--no-hooks", action="store_true", help=t("opt.no_hooks"))
    setup.add_argument("--dry-run", action="store_true", help=t("opt.dry_run"))
    setup.add_argument("--print-config", action="store_true", help=t("opt.print_config"))
    setup.add_argument("--remove", action="store_true", help=t("opt.remove"))

    add("doctor", cmd_doctor, path=False)
    hook = add("hook", cmd_hook, path=False)
    hook.add_argument("client")
    hook.add_argument("event", choices=("session-start", "stop"))

    add("where", cmd_where)
    add("serve", cmd_serve, path=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    ui.configure_streams()
    ui.reset()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HandoffError as exc:
        ui.error_console().print(Text(f"handoff: {t('error.prefix')} {exc}", style="error"))
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # The reader stopped early ('handoff log | head'): that is not an error. Point stdout
        # at the null device so the final flush at exit does not fail again.
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
