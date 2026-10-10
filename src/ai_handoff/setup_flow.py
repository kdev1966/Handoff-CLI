"""`handoff setup` and `handoff doctor`: the developer chooses, Handoff does the editing.

Every run shows the planned changes before writing anything, and asks for
confirmation unless --yes is given. Without a terminal and without --yes,
nothing is changed.
"""

from rich import box
from rich.table import Table
from rich.text import Text

from ai_handoff import integrations, paths, ui
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
            _say(f"  {integration.label}: {t('setup.manual', reason=integration.manual)}", "warning")
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


def run_setup(
    *, yes: bool, names: list[str] | None, hooks: bool | None, dry_run: bool, interactive: bool
) -> int:
    chosen = _select(names)
    if not chosen:
        _say(t("setup.none_detected"), "muted")
        return 0
    _say(t("setup.detected", clients=", ".join(integration.label for integration in chosen)), "bold")

    if not yes and not dry_run:
        if not interactive:
            _say(t("setup.non_interactive"), "warning")
            return 0
        choice = _choose(t("setup.menu"), [t("setup.all"), t("setup.pick"), t("setup.later")], "1")
        if choice == "3":
            _say(t("setup.skipped"), "muted")
            return 0
        if choice == "2":
            chosen = [item for item in chosen if ask_yes(t("setup.ask_client", client=item.label))]
        if hooks is None:
            hooks = ask_yes(t("setup.ask_hooks"))
    hooks = True if hooks is None else hooks

    preview = integrations.apply(chosen, hooks=hooks, dry_run=True)
    if not any(outcome.changes for outcome in preview):
        _print_outcomes(preview, preview=True)
        _say(t("setup.nothing_to_do"), "success")
        return 0
    _say(t("setup.planned"), "bold")
    _print_outcomes(preview, preview=True)
    if dry_run:
        return 0
    if not yes and not ask_yes(t("setup.confirm")):
        _say(t("purge.cancelled"), "muted")
        return 1

    outcomes = integrations.apply(chosen, hooks=hooks, dry_run=False)
    _say(t("setup.done"), "bold")
    _print_outcomes(outcomes, preview=False)
    _say(t("setup.backups", path=ui.short_path(str(paths.data_dir() / "backups"))), "muted")
    _say(t("setup.permissions_note"), "muted")
    return 1 if any(outcome.error for outcome in outcomes) else 0


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
    _say(tn("setup.removed_count", sum(1 for outcome in outcomes if outcome.changes)), "success")
    return 1 if any(outcome.error for outcome in outcomes) else 0


def _cell(value: bool | None) -> Text:
    if value is None:
        return Text("—", style="muted")
    return Text("✔", style="success") if value else Text("✗", style="error")


def doctor() -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for label in ("doctor.client", "doctor.mcp", "doctor.context", "doctor.save", "doctor.instructions"):
        table.add_column(t(label), justify="left" if label == "doctor.client" else "center")
    table.add_column(t("doctor.notes"))
    found = {integration.name for integration in integrations.detected()}
    for integration in INTEGRATIONS:
        state = (
            integrations.status(integration) if not integration.manual else dict.fromkeys(integrations.KINDS)
        )
        if integration.name not in found and not state["mcp"]:
            continue  # not installed here, and not connected either
        notes = []
        if integration.manual:
            notes.append(t("setup.manual", reason=integration.manual))
        elif state["mcp"] is False:
            notes.append(t("doctor.run_setup"))
        notes += integration.notes
        table.add_row(
            Text(integration.label, style="bold"),
            *(_cell(state[kind]) for kind in integrations.KINDS),
            Text("\n".join(notes), style="muted"),
        )
    return table
