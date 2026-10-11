# Changelog

All notable changes to Handoff are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-11

### Added

- English README by default, with the French version in `README.fr.md`.
- Screenshots of `handoff`, `log`, `show`, `diff` and `stats` in the README, generated from demo data by
  `scripts/screenshots.py`.
- The list of secret formats masked before storage, with a link to `redact.py`.
- This changelog.

### Changed

- The install scripts install the latest release from PyPI instead of the `main` branch.
  `HANDOFF_VERSION=0.1.0` pins a release; a branch name such as `main` installs unreleased code from GitHub.
- The README puts `pipx install ai-handoff` / `uv tool install ai-handoff` first and documents the data
  folder, the setup prompts and the release steps.

## [0.1.0] - 2026-10-11

First release.

### Added

- Local MCP server over stdio with three tools: `memory_get`, `memory_save` and `memory_history`.
  No network port, no raw SQL, no deletion by an AI.
- Append-only SQLite history per project, identified by the git remote (or the folder path), with the
  git branch and commit of each handoff.
- Secret masking before storage, input validation, and a generated, git-ignored `.handoff/context.md`
  for assistants without MCP.
- Guided setup of 14 AI tools (`handoff setup`, automatic or manual mode, preview and confirmation,
  backups, `--remove`) and `handoff doctor`. Handoff never pre-approves its tools.
- Optional hooks: the memory is injected at session start, and the assistant is asked once to save a
  handoff when the code changed. Claude Code hooks ship as the plugin `handoff@handoff`.
- Terminal views: `show`, `log` (graph by agent or branch), `diff` (word by word), `stats` (12-week
  heatmap), `list`, a welcome screen for `handoff` alone, and `--json` output.
- English and French interface, detected from the system; Tab completion for zsh, bash and PowerShell.
- One-command installers for macOS, Linux and Windows.

[0.1.1]: https://github.com/kdev1966/Handoff-CLI/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kdev1966/Handoff-CLI/releases/tag/v0.1.0
