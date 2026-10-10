"""User-facing language: detected automatically, overridable with HANDOFF_LANG.

Only text shown to people is translated. The MCP server, the context file and
anything read by AI assistants stays in English.
"""

import os
import re
import subprocess
import sys

from ai_handoff.locales import MESSAGES

DEFAULT_LANGUAGE = "en"
SUPPORTED = tuple(MESSAGES)
ENV_LANG = "HANDOFF_LANG"
# POSIX precedence for message language, after our own override.
_ENV_VARIABLES = (ENV_LANG, "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")

_current: str | None = None


def _language_from(value: str) -> str | None:
    """'fr_FR.UTF-8' -> 'fr'; 'de:fr' -> 'fr'; 'C' or unsupported -> None."""
    for part in value.split(":"):
        code = re.split(r"[_.@-]", part.strip(), maxsplit=1)[0].lower()
        if code in SUPPORTED:
            return code
    return None


def _system_language() -> str | None:
    if sys.platform == "win32":
        try:
            import ctypes
            import locale

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return _language_from(locale.windows_locale.get(lang_id, ""))
        except (AttributeError, OSError):
            return None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # Output is a list of preferred languages, most preferred first: ( "fr-TN", "ar-TN", en )
        for line in result.stdout.splitlines():
            language = _language_from(line.strip().strip('",() '))
            if language:
                return language
    return None


def detect_language() -> str:
    for variable in _ENV_VARIABLES:
        value = os.environ.get(variable)
        if value:
            # An explicit locale, even an unsupported one, wins over the system setting.
            return _language_from(value) or DEFAULT_LANGUAGE
    return _system_language() or DEFAULT_LANGUAGE


def get_language() -> str:
    global _current
    if _current is None:
        _current = detect_language()
    return _current


def set_language(language: str | None) -> None:
    """Force a language, or pass None to detect it again on next use."""
    global _current
    _current = language if language is None or language in SUPPORTED else DEFAULT_LANGUAGE


def t(message_key: str, /, **params: object) -> str:
    """Translate a message key, falling back to English."""
    template = MESSAGES[get_language()].get(message_key) or MESSAGES[DEFAULT_LANGUAGE][message_key]
    return template.format(**params) if params else template


def tn(message_key: str, count: int, /, **params: object) -> str:
    """Translate a message whose wording depends on a count ('key.one' / 'key.other')."""
    one = count in (0, 1) if get_language() == "fr" else count == 1
    return t(f"{message_key}.{'one' if one else 'other'}", count=count, **params)
