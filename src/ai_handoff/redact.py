"""Mask common secret formats before anything is stored.

Memory written under one assistant is later sent to other vendors, so secrets
must never reach the database. This is a safety net for well-known formats,
not a guarantee: an unknown token format will pass through.
"""

import re

MASK = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private key blocks
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        MASK,
    ),
    # Credentials embedded in URLs: scheme://user:password@host
    (re.compile(r"(?P<scheme>\b[a-zA-Z][\w+.-]*://)[^/\s:@]+:[^/\s@]+@"), rf"\g<scheme>{MASK}@"),
    # Provider tokens
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),  # AWS access key id
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), MASK),  # GitHub tokens
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), MASK),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), MASK),  # GitLab
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), MASK),  # OpenAI, Anthropic and similar
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), MASK),  # Slack
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),  # Google API key
    (re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b"), MASK),  # Stripe
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),  # JWT
    # key = value assignments whose name says "secret"
    (
        re.compile(
            r"(?i)(?P<name>\b[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|"
            r"auth[_-]?token|access[_-]?token|private[_-]?key|client[_-]?secret)\b)"
            r"(?P<sep>\s*[:=]\s*)(?!\[REDACTED\])"
            r"(?:\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s\"']{4,})"
        ),
        rf"\g<name>\g<sep>{MASK}",
    ),
)


def redact(text: str) -> tuple[str, int]:
    """Return text with secrets masked, and the number of masked values."""
    total = 0
    for pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total
