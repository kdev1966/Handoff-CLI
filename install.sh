#!/bin/sh
# Handoff installer for macOS and Linux.
#
#   Install or upgrade:
#     curl -fsSL https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.sh | sh
#   Uninstall (your memory database is kept):
#     curl -fsSL https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.sh | sh -s -- --uninstall
#
# Environment variables:
#   HANDOFF_VERSION=0.1.0      a released version from PyPI (default: the latest release),
#                              or a GitHub branch such as 'main' to try unreleased code
#   HANDOFF_SOURCE=<pip spec>  install from this pip requirement instead (a folder, a wheel...)
#   HANDOFF_NO_SETUP=1         skip the setup assistant (run 'handoff setup' later)
#   HANDOFF_SETUP_YES=1        connect every AI tool found, without questions
#   HANDOFF_NO_MODIFY_PATH=1   do not add ~/.local/bin to your shell profile
#
# Everything lives in a private virtual environment; nothing is installed system-wide
# and sudo is never used.

set -eu

REPO="kdev1966/Handoff-CLI"
PACKAGE="ai-handoff"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/handoff/venv"
BIN_DIR="$HOME/.local/bin"
PATH_MARKER="# added by the Handoff installer"

say() { printf '%s\n' "$*"; }
fail() {
    printf 'handoff-install: error: %s\n' "$*" >&2
    exit 1
}

find_python() {
    for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

profile_file() {
    case "$(basename "${SHELL:-sh}")" in
        zsh) printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash)
            if [ "$(uname -s)" = "Darwin" ]; then
                printf '%s\n' "$HOME/.bash_profile"
            else
                printf '%s\n' "$HOME/.bashrc"
            fi
            ;;
        *) printf '%s\n' "$HOME/.profile" ;;
    esac
}

add_to_path() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) return 0 ;;
    esac
    if [ -n "${HANDOFF_NO_MODIFY_PATH:-}" ]; then
        say "Add $BIN_DIR to your PATH to use the 'handoff' command."
        return 0
    fi
    profile="$(profile_file)"
    if ! grep -qF "$PATH_MARKER" "$profile" 2>/dev/null; then
        # shellcheck disable=SC2016 # $HOME and $PATH must be expanded by the shell profile, not now
        printf '\nexport PATH="$HOME/.local/bin:$PATH" %s\n' "$PATH_MARKER" >>"$profile"
        say "Added $BIN_DIR to your PATH in $profile."
    fi
    say "Open a new terminal (or run: . \"$profile\") to use the 'handoff' command."
}

remove_from_path() {
    for profile in "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
        if [ -f "$profile" ] && grep -qF "$PATH_MARKER" "$profile"; then
            tmp="$(mktemp)"
            grep -vF "$PATH_MARKER" "$profile" >"$tmp" || true
            cat "$tmp" >"$profile" # keeps the file's permissions and symlinks
            rm -f "$tmp"
            say "Removed the PATH line from $profile."
        fi
    done
}

run_setup() {
    if [ -n "${HANDOFF_NO_SETUP:-}" ]; then
        say "Run 'handoff setup' to connect Handoff to your AI tools."
    elif [ -n "$upgrade" ] && [ -z "${HANDOFF_SETUP_YES:-}" ]; then
        "$VENV_DIR/bin/handoff" completion --refresh >/dev/null 2>&1 || true
        say "Upgrade: your AI tools keep their configuration. Run 'handoff setup' to change it."
    elif [ -n "${HANDOFF_SETUP_YES:-}" ]; then
        "$VENV_DIR/bin/handoff" setup --yes || true
    elif (exec </dev/tty) 2>/dev/null; then
        # Under 'curl | sh' stdin is this script: ask the questions on the terminal instead.
        "$VENV_DIR/bin/handoff" setup </dev/tty || true
    else
        say "No terminal to ask you: run 'handoff setup' to connect Handoff to your AI tools."
    fi
}

package_source() {
    if [ -n "${HANDOFF_SOURCE:-}" ]; then
        printf '%s\n' "$HANDOFF_SOURCE"
        return
    fi
    case "${HANDOFF_VERSION:-}" in
        "") printf '%s\n' "$PACKAGE" ;; # the latest release on PyPI
        v[0-9]*) printf '%s==%s\n' "$PACKAGE" "${HANDOFF_VERSION#v}" ;;
        [0-9]*) printf '%s==%s\n' "$PACKAGE" "$HANDOFF_VERSION" ;;
        *) printf 'https://github.com/%s/archive/%s.tar.gz\n' "$REPO" "$HANDOFF_VERSION" ;; # a branch
    esac
}

install() {
    python="$(find_python)" || fail "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ or your package manager (macOS: 'brew install python'), then run this installer again."
    "$python" -c 'import venv, ensurepip' 2>/dev/null ||
        fail "$python cannot create virtual environments. On Debian/Ubuntu: sudo apt install python3-venv"

    source="$(package_source)"
    say "Installing Handoff with $("$python" --version 2>&1) from $source"

    upgrade=""
    if [ -x "$VENV_DIR/bin/handoff" ]; then
        upgrade=1
    fi
    mkdir -p "$(dirname "$VENV_DIR")"
    "$python" -m venv --clear "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --quiet --disable-pip-version-check "$source" ||
        fail "pip could not install $source"

    mkdir -p "$BIN_DIR"
    ln -sf "$VENV_DIR/bin/handoff" "$BIN_DIR/handoff"
    say "Installed $("$VENV_DIR/bin/handoff" --version) in $BIN_DIR/handoff"

    add_to_path
    run_setup
    say "Done. 'handoff doctor' shows how each AI tool is connected."
}

uninstall() {
    database=""
    if [ -x "$VENV_DIR/bin/handoff" ]; then
        database="$("$VENV_DIR/bin/python" -c 'from ai_handoff import paths; print(paths.db_path())' 2>/dev/null)" || true
        "$VENV_DIR/bin/handoff" setup --remove --yes || true
        "$VENV_DIR/bin/handoff" completion --uninstall >/dev/null 2>&1 || true
    fi
    if [ -L "$BIN_DIR/handoff" ]; then
        rm -f "$BIN_DIR/handoff"
    fi
    rm -rf "$VENV_DIR"
    rmdir "$(dirname "$VENV_DIR")" 2>/dev/null || true # only when empty
    remove_from_path
    say "Handoff is uninstalled. Your memory database was kept${database:+ in $database}; delete it by hand to erase it."
}

main() {
    case "${1:-}" in
        --uninstall) uninstall ;;
        "") install ;;
        *) fail "unknown option: $1 (use --uninstall to remove Handoff)" ;;
    esac
}

main "$@"
