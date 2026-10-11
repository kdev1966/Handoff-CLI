"""`handoff setup` and `handoff doctor`: the developer chooses, Handoff does the editing.

Every run shows the planned changes before writing anything, and asks for
confirmation unless --yes is given. Without a terminal and without --yes,
nothing is changed.
"""

import importlib.metadata
import json
import sys

from rich import box
from rich.table import Table
from rich.text import Text

from ai_handoff import completion, integrations, paths, ui
from ai_handoff.errors import HandoffError
from ai_handoff.i18n import t, tn
from ai_handoff.integrations import INTEGRATIONS, Integration, Outcome


def _say(message: str, style: str = "") -> None:
    ui.console().print(Text(message, style=style), soft_wrap=True)


def ask_yes(question: str, default: bool = True) -> bool:
    choices = t("confirm.default_yes") if default else t("confirm.choices")
    try:
        answer = input(f"{question} {choices} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes", "o", "oui"}


def _choose(question: str, options: list[str], default: str) -> str:
    for index, option in enumerate(options, start=1):
        _say(f"  {index}) {option}")
    try:
        answer = input(f"{question} [{default}] ").strip()
    except EOFError:
        return default
    return answer if answer in {str(index) for index in range(1, len(options) + 1)} else default


def _select(names: list[str] | None) -> list[Integration]:
    if not names:
        return integrations.detected()
    known = {integration.name: integration for integration in INTEGRATIONS}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise HandoffError(t("error.unknown_client", names=", ".join(unknown), known=", ".join(known)))
    return [known[name] for name in names]


def _print_outcomes(outcomes: list[Outcome], *, preview: bool) -> None:
    for outcome in outcomes:
        integration = outcome.integration
        if integration.manual:
            _say(f"  {integration.label}: {t('setup.manual', reason=t(integration.manual))}", "warning")
            continue
        if outcome.error:
            _say(f"  ✗ {integration.label}: {outcome.error}", "error")
            continue
        if not outcome.changes:
            _say(f"  {integration.label}: {t('setup.up_to_date')}", "muted")
            continue
        marker = "·" if preview else "✔"
        _say(f"  {marker} {integration.label}", "bold" if preview else "success")
        if preview:
            for change in outcome.changes:
                _say(f"      {ui.short_path(change)}", "muted")


CHOICES_FILE = "setup.json"


def load_choices() -> dict[str, object] | None:
    """What the developer chose at the last setup: the tools and whether hooks are wanted."""
    try:
        data = json.loads((paths.data_dir() / CHOICES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("clients"), list):
        return None

    def names(key: str) -> list[str]:
        value = data.get(key)
        return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []

    return {
        "clients": names("clients"),
        "declined": names("declined"),
        "hooks": data.get("hooks") is not False,
    }


def save_choices(names: list[str], hooks: bool, *, merge: bool, declined: list[str] | None = None) -> None:
    """Remember the tools configured, the tools the developer turned down, and the hooks choice."""
    previous = effective_choices() if merge else None
    clients = set(names) | set(previous["clients"] if previous else [])
    refused = (set(previous["declined"]) if previous else set()) | set(declined or [])
    data = {"clients": sorted(clients), "declined": sorted(refused - clients), "hooks": hooks}
    path = paths.data_dir() / CHOICES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _configured_now() -> tuple[list[str], bool]:
    """Tools where some Handoff configuration is in place, and whether any of them has hooks."""
    clients: list[str] = []
    hooks = False
    for integration in INTEGRATIONS:
        if integration.manual:
            continue
        state = integrations.status(integration)
        if any(state.values()):  # anything Handoff left there, even if part of it is now missing
            clients.append(integration.name)
            hooks = hooks or bool(state["context"] or state["save"])
    return clients, hooks


def effective_choices() -> dict[str, object] | None:
    """The saved choices, completed with every tool where Handoff is already configured.

    Installs older than 0.2.0 have no saved choices, and a first targeted run such as
    'handoff setup --client copilot' only names one tool: a tool with Handoff in place
    always counts as chosen.
    """
    saved = load_choices()
    present, hooks = _configured_now()
    if saved is None:
        return {"clients": present, "declined": [], "hooks": hooks} if present else None
    clients = sorted(set(saved["clients"]) | set(present))
    declined = [name for name in saved["declined"] if name not in clients]
    return {"clients": clients, "declined": declined, "hooks": saved["hooks"]}


def running_from_dev_copy() -> bool:
    """Whether this Handoff is an editable install (pip install -e), such as a clone being developed."""
    try:
        text = importlib.metadata.distribution("ai-handoff").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return False
    return is_editable(text)


def is_editable(direct_url: str | None) -> bool:
    """Read the direct_url.json that pip writes for an install from a folder or a URL."""
    try:
        data = json.loads(direct_url or "{}")
    except ValueError:
        return False
    return bool(
        isinstance(data, dict) and isinstance(data.get("dir_info"), dict) and data["dir_info"].get("editable")
    )


def _confirm_dev_copy(*, yes: bool, interactive: bool) -> bool:
    """Warn that the tools would start a development copy; return False to stop."""
    if not running_from_dev_copy():
        return True
    _say(t("setup.dev_copy", python=sys.executable), "warning")
    if yes or not interactive:
        return True
    return ask_yes(t("setup.dev_copy_continue"), default=False)


def clear_choices() -> None:
    (paths.data_dir() / CHOICES_FILE).unlink(missing_ok=True)


def run_setup(
    *, yes: bool, names: list[str] | None, hooks: bool | None, dry_run: bool, interactive: bool
) -> int:
    chosen = _select(names)
    detected_tools = list(chosen)
    if not chosen:
        _say(t("setup.none_detected"), "muted")
        return 0
    _say(t("setup.detected", clients=", ".join(integration.label for integration in chosen)), "bold")
    if not dry_run and not _confirm_dev_copy(yes=yes, interactive=interactive):
        _say(t("purge.cancelled"), "muted")
        return 1

    automatic = False
    # Tab completion goes with a full setup, not with a run targeting some tools.
    shell = completion.detect_shell() if not names else None
    want_completion = shell is not None and not completion.installed(shell)
    if not yes and not dry_run:
        if not interactive:
            _say(t("setup.non_interactive"), "warning")
            return 0
        mode = _choose(t("setup.mode"), [t("setup.mode_auto"), t("setup.mode_manual")], "1")
        if mode == "1":
            # Default values: every tool found, hooks on, no further question.
            automatic = yes = True
        else:
            choice = _choose(t("setup.menu"), [t("setup.all"), t("setup.pick"), t("setup.later")], "1")
            if choice == "3":
                _say(t("setup.skipped"), "muted")
                return 0
            if choice == "2":
                chosen = [item for item in chosen if ask_yes(t("setup.ask_client", client=item.label))]
            if hooks is None:
                hooks = ask_yes(t("setup.ask_hooks"))
            if want_completion:
                want_completion = ask_yes(t("setup.ask_completion", shell=shell))
    hooks = True if hooks is None else hooks

    preview = integrations.apply(chosen, hooks=hooks, dry_run=True)
    if not any(outcome.changes for outcome in preview):
        _print_outcomes(preview, preview=True)
        _say(t("setup.nothing_to_do"), "success")
        if not dry_run:
            save_choices(
                [item.name for item in chosen if not item.manual],
                hooks,
                merge=bool(names),
                declined=[item.name for item in detected_tools if item not in chosen and not item.manual],
            )
            if want_completion:
                _install_completion(shell)
        return 0
    if not automatic:
        _say(t("setup.planned"), "bold")
        _print_outcomes(preview, preview=True)
    if dry_run:
        return 0
    if not yes and not ask_yes(t("setup.confirm")):
        _say(t("purge.cancelled"), "muted")
        return 1

    outcomes = integrations.apply(chosen, hooks=hooks, dry_run=False)
    save_choices(
        [integration.name for integration in chosen if not integration.manual],
        hooks,
        merge=bool(names),
        declined=[item.name for item in detected_tools if item not in chosen and not item.manual],
    )
    _say(t("setup.done"), "bold")
    _print_outcomes(outcomes, preview=False)
    if want_completion:
        _install_completion(shell)
    _say(t("setup.backups", path=ui.short_path(str(paths.data_dir() / "backups"))), "muted")
    _say(t("setup.permissions_note"), "muted")
    return 1 if any(outcome.error for outcome in outcomes) else 0


def _install_completion(shell: str | None) -> None:
    from ai_handoff.cli import build_parser
    from ai_handoff.integrations.files import Session

    if shell is None:
        return
    try:
        profile = completion.install(shell, build_parser(), Session())
    except (FileNotFoundError, OSError):
        return  # completion is a convenience; setup goes on without it
    _say(f"  ✔ {t('completion.installed', shell=shell, profile=ui.short_path(str(profile)))}", "success")


def run_remove(*, yes: bool, dry_run: bool, interactive: bool) -> int:
    targets = [integration for integration in INTEGRATIONS if not integration.manual]
    preview = integrations.remove(targets, dry_run=True)
    if not any(outcome.changes for outcome in preview):
        _say(t("setup.nothing_to_remove"), "muted")
        return 0
    _say(t("setup.planned_removal"), "bold")
    _print_outcomes([outcome for outcome in preview if outcome.changes], preview=True)
    if dry_run:
        return 0
    if not yes and (not interactive or not ask_yes(t("setup.confirm_removal"), default=False)):
        _say(t("purge.cancelled"), "muted")
        return 1
    outcomes = integrations.remove(targets, dry_run=False)
    clear_choices()
    _say(tn("setup.removed_count", sum(1 for outcome in outcomes if outcome.changes)), "success")
    return 1 if any(outcome.error for outcome in outcomes) else 0


def run_fix(*, yes: bool, dry_run: bool, interactive: bool) -> int:
    """Repair what is missing in the tools chosen at setup, keeping the developer's choices."""
    choices = effective_choices()
    if choices is None:
        _say(t("doctor.no_choices"), "warning")
        return 0
    if not dry_run and not _confirm_dev_copy(yes=yes, interactive=interactive):
        _say(t("purge.cancelled"), "muted")
        return 1
    known = {integration.name: integration for integration in INTEGRATIONS}
    found = {integration.name for integration in integrations.detected()}
    chosen = [known[name] for name in choices["clients"] if name in known and name in found]
    new = [
        integration
        for integration in integrations.detected()
        if integration.name not in choices["clients"]
        and integration.name not in choices["declined"]
        and not integration.manual
    ]
    declined: list[str] = []
    if new:
        if interactive and not yes and not dry_run:
            for item in new:
                if ask_yes(t("doctor.ask_new", client=item.label)):
                    chosen.append(item)
                else:
                    declined.append(item.name)
        else:
            _say(t("doctor.new_tools", clients=", ".join(item.label for item in new)), "warning")
    hooks = bool(choices["hooks"])
    preview = integrations.apply(chosen, hooks=hooks, dry_run=True)
    if not any(outcome.changes for outcome in preview):
        if declined:
            save_choices([], hooks, merge=True, declined=declined)
        _say(t("doctor.all_good"), "success")
        return 0
    _say(t("setup.planned"), "bold")
    _print_outcomes([outcome for outcome in preview if outcome.changes or outcome.error], preview=True)
    if dry_run:
        return 0
    if not yes and (not interactive or not ask_yes(t("setup.confirm"))):
        _say(t("purge.cancelled"), "muted")
        return 1
    outcomes = integrations.apply(chosen, hooks=hooks, dry_run=False)
    save_choices([integration.name for integration in chosen], hooks, merge=True, declined=declined)
    _say(t("setup.done"), "bold")
    _print_outcomes([outcome for outcome in outcomes if outcome.changes or outcome.error], preview=False)
    return 1 if any(outcome.error for outcome in outcomes) else 0


def _cell(value: bool | None, *, off: bool = False) -> Text:
    if off:
        return Text(t("doctor.off"), style="muted")
    if value is None:
        return Text("—", style="muted")
    return Text("✔", style="success") if value else Text("✗", style="error")


def doctor() -> tuple[Table, bool]:
    """The state of each tool, and whether 'handoff doctor --fix' has something to do."""
    choices = effective_choices()
    chosen = set(choices["clients"]) if choices else set()
    hooks_off = choices is not None and not choices["hooks"]
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for label in ("doctor.client", "doctor.mcp", "doctor.context", "doctor.save", "doctor.instructions"):
        table.add_column(t(label), justify="left" if label == "doctor.client" else "center")
    table.add_column(t("doctor.notes"))
    found = {integration.name for integration in integrations.detected()}
    needs_fix = False
    for integration in INTEGRATIONS:
        state = (
            integrations.status(integration) if not integration.manual else dict.fromkeys(integrations.KINDS)
        )
        if integration.name not in found and not state["mcp"]:
            continue  # not installed here, and not connected either
        notes = []
        if integration.manual:
            notes.append(t("setup.manual", reason=t(integration.manual)))
        elif integration.name not in chosen:
            notes.append(t("doctor.run_setup") if state["mcp"] is False else "")
            if choices and integration.name not in choices["declined"]:
                needs_fix = True  # a tool installed since the last setup
        elif any(
            value is False and not (hooks_off and kind in ("context", "save"))
            for kind, value in state.items()
        ):
            needs_fix = True
        notes += [t(note) for note in integration.notes]
        table.add_row(
            Text(integration.label, style="bold"),
            *(
                _cell(state[kind], off=hooks_off and kind in ("context", "save") and state[kind] is not None)
                for kind in integrations.KINDS
            ),
            Text("\n".join(note for note in notes if note), style="muted"),
        )
    return table, needs_fix
