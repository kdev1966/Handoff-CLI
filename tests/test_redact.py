import pytest

from ai_handoff.redact import MASK, redact


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_" + "a1" * 18,
        "github_pat_" + "A" * 30,
        "glpat-" + "x" * 20,
        "sk-ant-api03-" + "Z" * 40,
        "sk-proj-" + "Q" * 30,
        "xoxb-1234567890-abcdefghij",
        "AIza" + "B" * 35,
        "sk_live_" + "9" * 24,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36",
    ],
)
def test_masks_provider_tokens(secret: str) -> None:
    text, count = redact(f"use {secret} for the deploy")
    assert secret not in text
    assert MASK in text
    assert count == 1


def test_masks_private_key_block() -> None:
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE=\n-----END OPENSSH PRIVATE KEY-----"
    text, count = redact(f"key:\n{block}\nend")
    assert "b3BlbnNzaC1rZXktdjE" not in text
    assert count == 1


def test_masks_url_credentials_but_keeps_host() -> None:
    text, count = redact("DATABASE_URL=postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in text
    assert "db.internal:5432/app" in text
    assert count >= 1


@pytest.mark.parametrize(
    "line",
    [
        'password = "correct horse"',
        "API_KEY=abcd1234efgh",
        "client_secret: s3cr3tvalue",
        "DB_PASSWORD='p4ssw0rd'",
    ],
)
def test_masks_secret_assignments_and_keeps_names(line: str) -> None:
    text, count = redact(line)
    name = line.split("=")[0].split(":")[0].strip()
    assert text.startswith(name)
    assert MASK in text
    assert count == 1


def test_leaves_ordinary_text_alone() -> None:
    text = "Refactored the password reset flow; tokens now expire after 15 minutes. See docs/api.md."
    assert redact(text) == (text, 0)
