"""Coverage for the ``python -m app.integrations`` entrypoint.

Mirrors the contract from ``tests/cli/test_main.py`` for the standalone
integrations CLI: Sentry must be initialised, accepted commands must emit a
single ``cli_invoked`` event with command metadata, ``--help`` and unknown
commands must skip analytics.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.integrations import __main__ as integrations_main


@pytest.fixture(autouse=True)
def _stub_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations_main, "init_sentry", lambda: None)
    monkeypatch.setattr(integrations_main, "shutdown_analytics", lambda **_kw: None)


def _captures(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object] | None]:
    captured: list[dict[str, object] | None] = []
    monkeypatch.setattr(
        integrations_main,
        "capture_first_run_if_needed",
        lambda: captured.append({"_marker": "install"}),
    )
    monkeypatch.setattr(
        integrations_main,
        "capture_cli_invoked",
        lambda properties=None: captured.append(properties),
    )
    return captured


def test_help_does_not_capture_analytics(monkeypatch, capsys) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m app.integrations", "--help"])

    integrations_main.main()

    assert captured == []
    assert "Commands: setup, list, show, remove, verify" in capsys.readouterr().out


def test_unknown_command_does_not_capture_analytics(monkeypatch, capsys) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m app.integrations", "bogus"])

    with pytest.raises(SystemExit) as excinfo:
        integrations_main.main()

    assert excinfo.value.code == 1
    assert captured == []
    assert "Unknown command" in capsys.readouterr().err


def test_list_emits_cli_invoked_with_metadata(monkeypatch) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m app.integrations", "list"])

    with patch.object(integrations_main, "cmd_list") as cmd_list:
        integrations_main.main()

    cmd_list.assert_called_once()
    install_marker, properties = captured
    assert install_marker == {"_marker": "install"}
    assert properties is not None
    assert properties["entrypoint"] == "python -m app.integrations"
    assert properties["command_path"] == "python -m app.integrations list"
    assert properties["command_family"] == "list"
    assert properties["command_leaf"] == "list"
    assert "subcommand" not in properties


def test_verify_emits_cli_invoked_with_service_subcommand(monkeypatch) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m app.integrations", "verify", "slack"])

    with (
        patch.object(integrations_main, "cmd_verify", return_value=0) as cmd_verify,
        pytest.raises(SystemExit) as excinfo,
    ):
        integrations_main.main()

    assert excinfo.value.code == 0
    cmd_verify.assert_called_once_with("slack", send_slack_test=False)
    install_marker, properties = captured
    assert install_marker == {"_marker": "install"}
    assert properties is not None
    assert properties["command_path"] == "python -m app.integrations verify slack"
    assert properties["command_family"] == "verify"
    assert properties["subcommand"] == "slack"
    assert properties["command_leaf"] == "slack"


def test_main_initialises_sentry(monkeypatch) -> None:
    init_calls: list[int] = []
    monkeypatch.setattr(integrations_main, "init_sentry", lambda: init_calls.append(1))
    _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m app.integrations", "--help"])

    integrations_main.main()

    assert init_calls == [1]
