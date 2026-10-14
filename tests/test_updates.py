"""The update check of 'handoff doctor'. The network is never reached: PyPI is replaced."""

import pytest

from ai_handoff import setup_flow, updates
from ai_handoff.cli import main


@pytest.mark.parametrize(
    ("prefix", "platform", "method"),
    [
        ("/home/me/.local/share/handoff/venv", "linux", "script"),
        ("/Users/me/.local/share/handoff/venv", "darwin", "script"),
        ("C:/Users/me/AppData/Local/handoff/venv", "win32", "script-windows"),
        ("/home/me/.local/share/pipx/venvs/ai-handoff", "linux", "pipx"),
        ("/home/me/.local/share/uv/tools/ai-handoff", "linux", "uv"),
        ("/usr/local", "linux", "pip"),
    ],
)
def test_install_method(prefix: str, platform: str, method: str) -> None:
    assert updates.install_method(prefix, platform) == method


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.3.1", "0.3.0", "0.3.1"),
        ("0.10.0", "0.9.9", "0.10.0"),
        ("0.3.0", "0.3.0", None),
        ("0.2.3", "0.3.0", None),
        ("0.4.0rc1", "0.3.0", None),  # pre-releases are not offered
        (None, "0.3.0", None),
    ],
)
def test_newer_release(latest: str | None, current: str, expected: str | None) -> None:
    assert updates.newer_release(latest, current) == expected


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed Handoff, with the check turned on."""
    monkeypatch.delenv(updates.ENV_OFF)
    monkeypatch.setattr(setup_flow, "running_from_dev_copy", lambda: False)
    monkeypatch.setattr(setup_flow, "doctor", lambda: ("", False))
    monkeypatch.setattr(updates, "install_method", lambda: "pipx")


def test_doctor_offers_the_upgrade_command(capsys, installed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: "99.0.0")
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "Handoff 99.0.0 is available" in out
    assert "Upgrade with: pipx upgrade ai-handoff" in out


def test_doctor_says_when_up_to_date(capsys, installed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: "0.0.1")
    main(["doctor"])
    assert "is the latest release (checked on PyPI" in capsys.readouterr().out


def test_doctor_is_silent_offline(capsys, installed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "latest_release", lambda: None)
    main(["doctor"])
    assert "PyPI" not in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["turned off", "development copy"])
def test_doctor_does_not_check(capsys, installed, monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    def no_network() -> str:
        raise AssertionError("PyPI was contacted")

    monkeypatch.setattr(updates, "latest_release", no_network)
    if reason == "turned off":
        monkeypatch.setenv(updates.ENV_OFF, "1")
    else:
        monkeypatch.setattr(setup_flow, "running_from_dev_copy", lambda: True)
    assert main(["doctor"]) == 0


def test_latest_release_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*_args, **_kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.latest_release() is None
