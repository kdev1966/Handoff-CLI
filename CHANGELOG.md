# Changelog

All notable changes to Handoff are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `handoff doctor` says whether a newer release is on PyPI, with the upgrade command that matches how
  Handoff was installed (install script, pipx, uv or pip). It is Handoff's only network access, made only
  when you run `doctor`; it says nothing when offline, and `HANDOFF_NO_UPDATE_CHECK=1` turns it off
  ([#21](https://github.com/kdev1966/Handoff-CLI/issues/21)).

## [0.3.0] - 2026-10-14

### Added

- Stale handoffs are pointed out. `memory_get`, the session start context and `handoff show` say how
  many commits the code moved since the latest handoff, or that the current code is behind it or on another
  line of history
  ([#15](https://github.com/kdev1966/Handoff-CLI/issues/15)).
- Diverged handoffs are detected ([#15](https://github.com/kdev1966/Handoff-CLI/issues/15)). Each handoff
  records the one its session started from (`based_on`): the server knows it after `memory_get`, and the
  session start context gives the number to pass to `memory_save`. When other handoffs were saved in
  between, `memory_save` says so, `memory_get` and the session start context show them next to the latest
  one (up to three in full), `handoff show` names them with the `handoff diff` to compare, and `handoff log`
  marks the handoff. Suggested on r/mcp.
- Export and import keep these links, with the numbers of the receiving database.

### Changed

- The database schema moves to version 4 (one new column); existing databases are upgraded automatically.
- `memory_get` and `memory_history` declare all four MCP tool hints (read-only, non-destructive, idempotent,
  closed world), since some directories reject tools that leave any of them out.

## [0.2.3] - 2026-10-11

### Added

- `memory_save` and `handoff save` warn, without refusing, when the summary or the next actions are over
  4 KB, since the latest handoff is read at the start of every session; the hard limit stays 16 KB
  ([#14](https://github.com/kdev1966/Handoff-CLI/issues/14)).
- `memory_save` warns when the assistant gives no agent name, so every handoff in the history says who wrote
  it ([#17](https://github.com/kdev1966/Handoff-CLI/issues/17)).

## [0.2.2] - 2026-10-11

### Added

- A "How Handoff compares" section in the README (English and French).
- Issue templates for bug reports and feature requests.
- `handoff setup` and `handoff doctor --fix` warn when they run from a development copy (editable install),
  whose path the AI tools would then start, and ask before going on.

### Fixed

- When a tool's own command fails (for example `copilot mcp add` on Copilot CLI versions before 1.0),
  Handoff now writes the tool's configuration file instead of reporting an error.
- Known agent names ignore case: "Copilot" and "copilot" are the same lane.
- A tool where Handoff is already configured always counts as chosen. Installs older than 0.2.0 and a
  first targeted `handoff setup --client …` no longer make `doctor --fix` report configured tools as new.
- The notes of `handoff doctor` follow the interface language.

## [0.2.1] - 2026-10-11

### Fixed

- The help of every subcommand showed the usage prefix twice (`usage: usage: handoff log …`).

## [0.2.0] - 2026-10-11

### Added

- `handoff export` writes a project's memory (or all of it with `--all`) to JSON Lines, to standard output
  or to a file readable by you only.
- `handoff import` adds the handoffs of an export without duplicates, keeping dates, agents, branches and
  commits. The file is untrusted: every field is validated and secrets are masked again. `--path` attaches
  the memory of a project without a git remote to a local folder; `--dry-run` previews.
- `handoff where` explains which memory a folder uses and why (git remote or folder path, root), warns when
  the memory will not follow a moved folder or when you are in a subfolder, and accepts `--json`.
- `handoff doctor --fix` repairs what is missing in the tools chosen at setup and offers tools installed
  since, after showing the changes. `handoff setup` now remembers the tools configured, the tools declined
  and the hooks choice; `doctor` shows declined hooks as off instead of missing.
- README sections on backups and syncing machines.

### Changed

- Handoffs are ordered by date rather than by insertion, so an imported older handoff never becomes the
  latest.
- The uninstall scripts ask Python for the database path instead of parsing `handoff where`.

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

[0.3.0]: https://github.com/kdev1966/Handoff-CLI/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/kdev1966/Handoff-CLI/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/kdev1966/Handoff-CLI/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/kdev1966/Handoff-CLI/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/kdev1966/Handoff-CLI/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/kdev1966/Handoff-CLI/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/kdev1966/Handoff-CLI/releases/tag/v0.1.0
