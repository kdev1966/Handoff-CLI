"""Command-line interface: the same memory as the MCP server, plus administration."""

import argparse
import sys
from pathlib import Path

from ai_handoff import __version__, paths
from ai_handoff.context_file import context_path, remove_context, write_context
from ai_handoff.errors import HandoffError
from ai_handoff.project import Project, resolve_project
from ai_handoff.setup_clients import mcp_config_json, setup_claude_code
from ai_handoff.store import MAX_HISTORY, Store


def _project(path: str | None) -> Project:
    return resolve_project(Path(path).absolute() if path else Path.cwd())


def _read_value(value: str | None) -> str | None:
    """'-' reads the value from standard input, for multi-line text."""
    return sys.stdin.read() if value == "-" else value


def cmd_serve(args: argparse.Namespace) -> int:
    from ai_handoff.server import serve

    serve()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        paused = store.is_paused(project.key)
        handoff = store.latest(project.key)
    status = " (paused)" if paused else ""
    print(f"Project: {project.name}{status}\nKey:     {project.key}\nRoot:    {project.root}\n")
    print(handoff.to_text() if handoff else "No handoff saved yet.")
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    if [args.summary, args.next, args.stack].count("-") > 1:
        raise HandoffError("Only one of --summary, --next and --stack can be read from stdin ('-').")
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
    print(f"Saved handoff #{handoff.id} for {project.name}.")
    if handoff.redactions:
        print(f"{handoff.redactions} secret-looking value(s) were masked.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        handoffs = store.history(project.key, limit=args.limit)
    if not handoffs:
        print(f"No handoff saved yet for {project.name}.")
        return 0
    print("\n\n---\n\n".join(handoff.to_text() for handoff in handoffs))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        if store.is_paused(project.key):
            raise HandoffError(f"Handoff is paused for {project.name}; run 'handoff resume' first.")
        handoff = store.latest(project.key)
    print(write_context(project, handoff))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with Store() as store:
        records = store.list_projects()
    if not records:
        print("No project yet.")
        return 0
    for record in records:
        status = "paused" if record.paused else f"{record.handoffs} handoff(s)"
        print(f"{record.name}  [{status}, last: {record.last_handoff_at or 'never'}]")
        print(f"    key:  {record.key}")
        print(f"    root: {record.root}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        store.set_paused(project, True)
    remove_context(project)
    print(f"Paused {project.name}: assistants can no longer read or save its memory.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        store.set_paused(project, False)
        handoff = store.latest(project.key)
    if handoff:
        write_context(project, handoff)
    print(f"Resumed {project.name}.")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    with Store() as store:
        if args.key:
            record = store.get_project(args.key)
            if not record:
                raise HandoffError(f"No project with key {args.key!r}. See 'handoff list'.")
            project = Project(key=record.key, root=Path(record.root), name=record.name)
        else:
            project = _project(args.path)
            if not store.get_project(project.key):
                raise HandoffError(f"{project.name} has no memory to purge.")
        if not args.yes and not _confirm(f"Delete the whole memory of {project.name} ({project.key})?"):
            print("Cancelled.")
            return 1
        count = store.purge(project.key)
    if project.root.is_dir():
        remove_context(project)
    print(f"Purged {project.name}: {count} handoff(s) deleted.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    project = _project(args.path)
    with Store() as store:
        handoff = store.restore(args.id, project)
    write_context(project, handoff)
    print(f"Restored handoff #{args.id} as #{handoff.id} for {project.name}.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if args.print_config:
        print(mcp_config_json())
        return 0
    return setup_claude_code(args.scope, args.dry_run)


def cmd_where(args: argparse.Namespace) -> int:
    print(f"Database:     {paths.db_path()}")
    try:
        print(f"Context file: {context_path(_project(args.path))}")
    except HandoffError as exc:
        print(f"Context file: (no project here: {exc})")
    return 0


def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes", "o", "oui"}
    except EOFError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handoff",
        description="Shared, persistent memory that lets AI assistants hand work over to each other.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, func, help: str, *, path: bool = True) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help, description=help)
        command.set_defaults(func=func)
        if path:
            command.add_argument("--path", "-p", help="project folder (default: current directory)")
        return command

    add("serve", cmd_serve, "run the MCP server over stdio (used by AI clients)", path=False)
    add("show", cmd_show, "show the latest handoff of a project")

    save = add("save", cmd_save, "save a handoff (for assistants without MCP); '-' reads stdin")
    save.add_argument("--summary", "-s", required=True, help="current state of the work")
    save.add_argument("--next", "-n", default="", help="next actions for whoever continues")
    save.add_argument("--stack", help="tech stack (default: keep the previous one)")
    save.add_argument("--agent", "-a", default="cli", help="who is saving (default: cli)")

    history = add("history", cmd_history, "list previous handoffs, newest first")
    history.add_argument(
        "--limit", "-l", type=int, default=10, choices=range(1, MAX_HISTORY + 1), metavar="N"
    )

    add("render", cmd_render, "regenerate .handoff/context.md")
    add("list", cmd_list, "list every project with a memory", path=False)
    add("pause", cmd_pause, "stop tracking a project (confidential work)")
    add("resume", cmd_resume, "resume tracking a paused project")

    purge = add("purge", cmd_purge, "delete a project's memory and history")
    purge.add_argument("--key", help="purge by key (see 'handoff list'), e.g. for a deleted folder")
    purge.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")

    restore = add("restore", cmd_restore, "make an older handoff the latest again")
    restore.add_argument("id", type=int, help="handoff id (see 'handoff history')")

    setup = add("setup", cmd_setup, "register the MCP server with Claude Code", path=False)
    setup.add_argument("--scope", choices=("user", "local", "project"), default="user")
    setup.add_argument("--dry-run", action="store_true", help="print the command without running it")
    setup.add_argument(
        "--print-config", action="store_true", help="print a JSON config for other MCP clients"
    )

    add("where", cmd_where, "show where Handoff stores its data")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # never crash on a console that cannot print a character
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HandoffError as exc:
        print(f"handoff: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
