"""Register the MCP server with AI clients."""

import json
import shlex
import shutil
import subprocess
import sys

SERVER_NAME = "handoff"


def server_command() -> list[str]:
    """Command that starts the server with the interpreter Handoff is installed in.

    Using the absolute interpreter path keeps it working when 'handoff' is not on
    the PATH of the client (GUI apps on macOS and Windows often have a minimal PATH).
    """
    return [sys.executable, "-m", "ai_handoff", "serve"]


def mcp_config_json() -> str:
    command, *args = server_command()
    return json.dumps({"mcpServers": {SERVER_NAME: {"command": command, "args": args}}}, indent=2)


def claude_code_command(scope: str) -> list[str]:
    claude = shutil.which("claude") or "claude"
    return [claude, "mcp", "add", "--scope", scope, SERVER_NAME, "--", *server_command()]


def claude_code_remove_command(scope: str) -> list[str]:
    claude = shutil.which("claude") or "claude"
    return [claude, "mcp", "remove", "--scope", scope, SERVER_NAME]


def format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)


def setup_claude_code(scope: str, dry_run: bool, remove: bool = False) -> int:
    """Register (or with remove=True, unregister) the server in Claude Code.

    Registering again replaces the previous entry, so re-running after an
    upgrade or a reinstall keeps the command path up to date.
    """
    remove_command = claude_code_remove_command(scope)
    add_command = claude_code_command(scope)
    if dry_run:
        print(format_command(remove_command if remove else add_command))
        return 0
    if not shutil.which("claude"):
        print("Claude Code ('claude') was not found on PATH.", file=sys.stderr)
        return 1

    removed = subprocess.run(remove_command, capture_output=True, check=False).returncode == 0
    if remove:
        print(f"Removed '{SERVER_NAME}' from Claude Code." if removed else "Handoff was not registered.")
        return 0
    result = subprocess.run(add_command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"Registration in Claude Code failed:\n{result.stderr or result.stdout}", file=sys.stderr)
        return result.returncode
    action = "Updated" if removed else "Registered"
    print(f"{action} '{SERVER_NAME}' in Claude Code ({scope} scope).")
    return 0
