"""What is known about a project's latest handoff beyond its text.

- Drift: how far the code moved since the commit the handoff was written at.
- Divergence: handoffs saved between the one its author started from and itself,
  which that author never saw. Append-only history keeps them, but the latest
  handoff may leave their work out, so they are shown next to it.
"""

from ai_handoff.i18n import t, tn
from ai_handoff.project import Project, git_drift
from ai_handoff.store import Handoff, Store

MAX_INTERVENING = 3  # shown in full to an assistant; the rest through memory_history
MAX_LISTED = 5  # named in a divergence note


def drift_note(project: Project, handoff: Handoff) -> str | None:
    drift = git_drift(project.root, handoff.git_commit)
    if drift is None or handoff.git_commit is None:
        return None
    commit = handoff.git_commit[:7]
    if drift.kind == "ahead":
        return tn("drift.ahead", drift.commits, commit=commit, now=drift.now)
    return t(f"drift.{drift.kind}", commit=commit, now=drift.now)


def base_label(based_on: int) -> str:
    return f"#{based_on}" if based_on else t("diverged.empty")


def divergence_note(handoff: Handoff, intervening: list[Handoff]) -> str | None:
    if not intervening or handoff.based_on is None:
        return None
    oldest_first = sorted(intervening, key=lambda other: other.id)
    names = ", ".join(
        t("diverged.other", id=other.id, agent=other.agent) for other in oldest_first[:MAX_LISTED]
    )
    if len(oldest_first) > MAX_LISTED:
        names += " " + t("diverged.and_more", count=len(oldest_first) - MAX_LISTED)
    return tn("diverged.note", len(intervening), base=base_label(handoff.based_on), others=names)


def latest_text(store: Store, project: Project) -> str | None:
    """The latest handoff for an assistant, with its notes and the handoffs it did not see."""
    handoff = store.latest(project.key)
    if handoff is None:
        return None
    intervening = store.intervening(handoff)
    notes = tuple(
        note for note in (drift_note(project, handoff), divergence_note(handoff, intervening)) if note
    )
    text = handoff.to_text(notes)
    if not intervening:
        return text
    sections = [other.to_text() for other in intervening[:MAX_INTERVENING]]
    hidden = len(intervening) - MAX_INTERVENING
    if hidden > 0:
        sections.append(tn("diverged.more", hidden))
    return f"{text}\n\n---\n\n{t('diverged.heading')}\n\n" + "\n\n---\n\n".join(sections)
