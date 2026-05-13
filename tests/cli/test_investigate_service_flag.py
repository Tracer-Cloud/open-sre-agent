"""Tests for the --service flag on the investigate CLI command."""

from __future__ import annotations

from typing import NoReturn
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.commands.general import investigate_command


def _fake_payload(service: str) -> dict[str, object]:
    return {
        "alert_name": f"Remote runtime investigation: {service}",
        "pipeline_name": service,
        "severity": "warning",
        "investigation_origin": "remote_runtime",
        "service": {"provider": "railway"},
        "recent_logs": "sample logs",
        "health_probe": {"status_code": 200},
    }


def _fake_result() -> dict[str, object]:
    return {
        "report": "report body",
        "problem_md": "# problem",
        "root_cause": "bad deploy",
        "is_noise": False,
    }


def test_service_flag_invokes_runtime_investigation(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    runner = CliRunner()
    with (
        patch(
            "app.remote.runtime_alert.build_runtime_alert_payload",
            return_value=_fake_payload("my-svc"),
        ) as mock_build,
        patch(
            "app.cli.investigation.run_investigation_cli",
            return_value=_fake_result(),
        ) as mock_run,
    ):
        result = runner.invoke(investigate_command, ["--service", "my-svc"])

    assert result.exit_code == 0
    mock_build.assert_called_once_with("my-svc", slack_thread_ref=None, slack_bot_token=None)
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["alert_name"] == "Remote runtime investigation: my-svc"
    assert kwargs["raw_alert"]["service"]["provider"] == "railway"


def test_service_flag_writes_output_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    runner = CliRunner()
    output_path = tmp_path / "result.json"

    with (
        patch(
            "app.remote.runtime_alert.build_runtime_alert_payload",
            return_value=_fake_payload("my-svc"),
        ),
        patch(
            "app.cli.investigation.run_investigation_cli",
            return_value=_fake_result(),
        ),
    ):
        result = runner.invoke(
            investigate_command,
            ["--service", "my-svc", "--output", str(output_path)],
        )

    assert result.exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert "report body" in content
    assert "bad deploy" in content


@pytest.mark.parametrize(
    "conflict_flag,conflict_value",
    [
        ("--input", "/tmp/alert.json"),
        ("--input-json", '{"alert_name":"x"}'),
        ("--interactive", None),
        ("--print-template", "generic"),
    ],
)
def test_service_flag_rejects_other_input_modes(conflict_flag, conflict_value) -> None:
    runner = CliRunner()
    args = ["--service", "my-svc", conflict_flag]
    if conflict_value is not None:
        args.append(conflict_value)

    with (
        patch("app.remote.runtime_alert.build_runtime_alert_payload"),
        patch("app.cli.investigation.run_investigation_cli"),
    ):
        result = runner.invoke(investigate_command, args)

    assert result.exit_code != 0
    assert "--service cannot be combined with" in (result.output + str(result.exception))


def test_service_flag_surfaces_errors_from_payload_builder() -> None:
    from app.cli.support.errors import OpenSREError

    runner = CliRunner()
    with (
        patch("app.cli.commands.general.track_investigation") as mock_tracking,
        patch(
            "app.remote.runtime_alert.build_runtime_alert_payload",
            side_effect=OpenSREError("unknown service", suggestion="add it"),
        ),
        patch("app.cli.investigation.run_investigation_cli"),
    ):
        result = runner.invoke(investigate_command, ["--service", "missing"])

    assert result.exit_code != 0
    mock_tracking.assert_not_called()


def test_print_template_does_not_count_as_investigation() -> None:
    runner = CliRunner()

    with patch("app.cli.commands.general.track_investigation") as mock_tracking:
        result = runner.invoke(investigate_command, ["--print-template", "generic"])

    assert result.exit_code == 0
    mock_tracking.assert_not_called()


def test_slack_thread_without_service_is_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(
        investigate_command,
        ["--slack-thread", "C01234/1712345.000001"],
    )
    assert result.exit_code != 0
    assert "--slack-thread requires --service" in (result.output + str(result.exception))


def test_slack_thread_without_bot_token_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    runner = CliRunner()

    with (
        patch(
            "app.remote.runtime_alert.build_runtime_alert_payload",
            return_value=_fake_payload("my-svc"),
        ),
        patch("app.cli.investigation.run_investigation_cli", return_value=_fake_result()),
    ):
        result = runner.invoke(
            investigate_command,
            ["--service", "my-svc", "--slack-thread", "C01234/1712345.000001"],
        )

    assert result.exit_code != 0
    assert "SLACK_BOT_TOKEN is not set" in (result.output + str(result.exception))


def test_slack_thread_passed_to_payload_builder(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake-token")
    runner = CliRunner()

    with (
        patch(
            "app.remote.runtime_alert.build_runtime_alert_payload",
            return_value=_fake_payload("my-svc"),
        ) as mock_build,
        patch("app.cli.investigation.run_investigation_cli", return_value=_fake_result()),
    ):
        result = runner.invoke(
            investigate_command,
            ["--service", "my-svc", "--slack-thread", "C01234/1712345.000001"],
        )

    assert result.exit_code == 0
    mock_build.assert_called_once_with(
        "my-svc",
        slack_thread_ref="C01234/1712345.000001",
        slack_bot_token="xoxb-fake-token",
    )


def test_investigate_command_keyboard_interrupt_non_streaming(monkeypatch) -> None:
    """Ctrl+C during a non-streaming investigation exits cleanly with code 0."""
    runner = CliRunner()

    def fake_run(*args: object, **kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr("app.cli.investigation.run_investigation_cli", fake_run)
    monkeypatch.setattr(
        "app.cli.investigation.payload.load_payload", lambda **_: {"alert_name": "A"}
    )
    monkeypatch.setattr("app.cli.commands.general.is_json_output", lambda: True)

    result = runner.invoke(investigate_command, ["--input", "/tmp/alert.json"])
    assert result.exit_code == 0
    assert result.exception is None


def test_investigate_command_keyboard_interrupt_streaming(monkeypatch) -> None:
    """Ctrl+C during a streaming investigation exits cleanly with code 0."""
    import sys as real_sys
    import types
    from unittest.mock import MagicMock

    runner = CliRunner()

    def fake_streaming(*args: object, **kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr("app.cli.investigation.run_investigation_cli_streaming", fake_streaming)
    monkeypatch.setattr(
        "app.cli.investigation.payload.load_payload", lambda **_: {"alert_name": "A"}
    )
    monkeypatch.setattr("app.cli.commands.general.is_json_output", lambda: False)

    # Click's CliRunner patches the real sys.stdout, but
    # app.cli.commands.general imported sys at module load time.
    # Replace it with a fake module whose stdout reports isatty=True
    # so the command takes the streaming path.
    fake_sys = types.ModuleType("sys")
    fake_sys.__dict__.update(real_sys.__dict__)
    fake_sys.stdout = MagicMock()
    fake_sys.stdout.isatty.return_value = True

    with patch("app.cli.commands.general.sys", fake_sys):
        result = runner.invoke(investigate_command, ["--input", "/tmp/alert.json"])

    assert result.exit_code == 0
    assert result.exception is None
