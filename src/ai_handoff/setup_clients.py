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


def format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)


def setup_claude_code(scope: str, dry_run: bool) -> int:
    command = claude_code_command(scope)
    if dry_run:
        print(format_command(command))
        return 0
    if not shutil.which("claude"):
        print("Claude Code ('claude') was not found on PATH.", file=sys.stderr)
        return 1
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(
            f"Registration failed. If a server named '{SERVER_NAME}' already exists, remove it with "
            f"'claude mcp remove {SERVER_NAME} --scope {scope}' and try again.",
            file=sys.stderr,
        )
    return result.returncode
