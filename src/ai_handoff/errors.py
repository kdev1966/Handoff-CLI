class HandoffError(Exception):
    """An expected failure whose message is safe to show to a user or an agent."""


class ProjectError(HandoffError):
    """The given path cannot be used as a project."""


class ValidationError(HandoffError):
    """A handoff field is missing, too large or malformed."""


class PausedError(HandoffError):
    """Tracking is paused for this project."""


class NotFoundError(HandoffError):
    """The requested project or handoff does not exist."""
