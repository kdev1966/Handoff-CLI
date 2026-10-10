import re
import string
from pathlib import Path

import pytest

from ai_handoff import i18n
from ai_handoff.locales import MESSAGES

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ai_handoff"
_KEY_CALL = re.compile(r"""\bt\(\s*["']([a-z_.]+)["']|\btn\(\s*["']([a-z_.]+)["']""")


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_language_has_the_same_keys_and_placeholders() -> None:
    english = MESSAGES["en"]
    for language, catalog in MESSAGES.items():
        assert catalog.keys() == english.keys(), language
        for key, template in catalog.items():
            assert placeholders(template) == placeholders(english[key]), (language, key)


def test_every_key_used_in_the_code_exists() -> None:
    used: set[str] = set()
    plural: set[str] = set()
    for path in SOURCE.glob("*.py"):
        for single, counted in _KEY_CALL.findall(path.read_text(encoding="utf-8")):
            (used if single else plural).add(single or counted)
    catalog = MESSAGES["en"]
    missing = {key for key in used if key not in catalog}
    missing |= {key for key in plural if f"{key}.one" not in catalog or f"{key}.other" not in catalog}
    assert not missing


def test_command_help_exists_for_every_command() -> None:
    from ai_handoff.cli import build_parser

    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    assert all(f"cmd.{name}" in MESSAGES["en"] for name in commands)


@pytest.mark.parametrize(
    ("variables", "expected"),
    [
        ({"HANDOFF_LANG": "fr"}, "fr"),
        ({"HANDOFF_LANG": "en", "LANG": "fr_FR.UTF-8"}, "en"),
        ({"LANG": "fr_FR.UTF-8"}, "fr"),
        ({"LC_ALL": "fr_CA.UTF-8", "LANG": "en_US.UTF-8"}, "fr"),
        ({"LANGUAGE": "de:fr", "LANG": "en_US.UTF-8"}, "fr"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LANG": "C"}, "en"),
    ],
)
def test_detects_language_from_environment(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], expected: str
) -> None:
    for name in ("HANDOFF_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(i18n, "_system_language", lambda: "fr")
    assert i18n.detect_language() == expected


def test_falls_back_to_system_language_then_english(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HANDOFF_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(i18n, "_system_language", lambda: "fr")
    assert i18n.detect_language() == "fr"
    monkeypatch.setattr(i18n, "_system_language", lambda: None)
    assert i18n.detect_language() == "en"


@pytest.mark.parametrize(
    ("language", "count", "expected"),
    [
        ("en", 0, "0 handoffs"),
        ("en", 1, "1 handoff"),
        ("en", 2, "2 handoffs"),
        ("fr", 0, "0 passation"),
        ("fr", 1, "1 passation"),
        ("fr", 2, "2 passations"),
    ],
)
def test_plural_forms(language: str, count: int, expected: str) -> None:
    i18n.set_language(language)
    assert i18n.tn("count.handoffs", count) == expected


def test_unsupported_forced_language_falls_back_to_english() -> None:
    i18n.set_language("xx")
    assert i18n.get_language() == "en"
