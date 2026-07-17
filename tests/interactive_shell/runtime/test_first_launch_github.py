from __future__ import annotations

import io

import pytest
from rich.console import Console

from integrations.github import login as github_login_mod
from integrations.github.login import GitHubLoginResult
from integrations.github.mcp import DEFAULT_GITHUB_MCP_TOOLSETS, DEFAULT_GITHUB_MCP_URL
from integrations.github.mcp_oauth import GitHubDeviceCode
from platform.analytics import source as analytics_source
from platform.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime.startup import first_launch_github as flg


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _terminal_console(output: io.StringIO) -> Console:
    return Console(file=output, force_terminal=True, color_system="truecolor", highlight=False)


def _force_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every gate input so that login would be required."""
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.setattr(flg, "is_test_run", lambda: False)
    monkeypatch.setattr(flg, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(flg, "_github_already_configured", lambda: False)
    monkeypatch.setattr(flg, "read_github_login_deferred", lambda: False)


def test_gate_required_when_all_conditions_met(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    assert flg.should_require_github_login() is True


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_gate_skipped_by_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _force_required(monkeypatch)
    monkeypatch.setenv("OPENSRE_SKIP_GITHUB_LOGIN", value)
    assert flg.should_require_github_login() is False


def test_gate_skipped_in_test_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "is_test_run", lambda: True)
    assert flg.should_require_github_login() is False


def test_gate_required_on_linux_when_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    assert flg.should_require_github_login() is True


def test_gate_skipped_when_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "repl_tty_interactive", lambda: False)
    assert flg.should_require_github_login() is False


def test_gate_skipped_when_github_already_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "_github_already_configured", lambda: True)
    assert flg.should_require_github_login() is False


def test_gate_required_when_github_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: GitHub config is authoritative. A prior completed login (no
    longer recorded via any standalone marker) must not let the REPL start once
    the GitHub integration has been removed."""
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "_github_already_configured", lambda: False)
    assert flg.should_require_github_login() is True


@pytest.mark.parametrize(
    "ci_env",
    [
        ("CI", "true"),
        ("GITHUB_ACTIONS", "true"),
        ("OPENSRE_IS_TEST", "1"),
    ],
)
def test_gate_skipped_in_ci_like_environment(
    monkeypatch: pytest.MonkeyPatch, ci_env: tuple[str, str]
) -> None:
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("OPENSRE_INVESTIGATION_SOURCE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(flg, "is_test_run", analytics_source.is_test_run)
    monkeypatch.setattr(flg, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(flg, "_github_already_configured", lambda: False)
    monkeypatch.setenv(ci_env[0], ci_env[1])
    assert flg.should_require_github_login() is False


def test_gate_skipped_when_github_login_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "read_github_login_deferred", lambda: True)
    assert flg.should_require_github_login() is False


def test_gate_required_when_stale_github_store_record_has_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy installs may have an abandoned github store row without credentials."""
    _force_required(monkeypatch)
    monkeypatch.setattr(
        "integrations.store.get_integration",
        lambda service: (
            {
                "credentials": {
                    "mode": "streamable-http",
                    "url": DEFAULT_GITHUB_MCP_URL,
                    "toolsets": list(DEFAULT_GITHUB_MCP_TOOLSETS),
                }
            }
            if service == "github"
            else None
        ),
    )
    assert flg.should_require_github_login() is True


def test_device_code_prompt_highlights_user_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    ui_theme.set_active_theme("blue")
    output = io.StringIO()
    code = GitHubDeviceCode(
        device_code="dev-123",
        user_code="WXYZ-1234",
        verification_uri="https://github.com/login/device",
        expires_in=900,
        interval=5,
    )

    flg._show_device_code(_terminal_console(output), code, allow_skip=True)

    rendered = output.getvalue()
    assert f"{ui_theme.DEVICE_CODE_ANSI}WXYZ-1234" in rendered


def test_orchestrator_success_proceeds_and_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "control")
    monkeypatch.setattr(flg, "_offer_github_login", lambda _console, **_kwargs: True)
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=True, username="octocat", detail="OK"),
    )
    completed: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        flg,
        "capture_github_login_completed",
        lambda username, *, variant=None: completed.append((username, variant)),
    )
    cleared: list[bool] = []
    monkeypatch.setattr(flg, "clear_github_login_deferral", lambda: cleared.append(True))
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert completed == [("octocat", "control")]
    assert cleared == [True]


def test_orchestrator_skip_at_menu_proceeds_in_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "control")
    monkeypatch.setattr(flg, "_offer_github_login", lambda _console, **_kwargs: False)
    deferred: list[bool] = []
    skipped: list[str] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", deferred.append)
    monkeypatch.setattr(
        flg,
        "capture_github_login_skipped",
        lambda *, variant: skipped.append(variant),
    )
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert deferred == [True]
    assert skipped == ["control"]


def test_orchestrator_escape_during_wait_proceeds_in_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "control")
    monkeypatch.setattr(flg, "_offer_github_login", lambda _console, **_kwargs: True)

    def _raise_cancel(**_kwargs: object) -> GitHubLoginResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(github_login_mod, "authenticate_and_configure_github", _raise_cancel)
    deferred: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", deferred.append)
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "capture_github_login_skipped", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert deferred == [True]


def test_orchestrator_failure_then_decline_retry_skips_in_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "control")
    monkeypatch.setattr(flg, "_offer_github_login", lambda _console, **_kwargs: True)
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=False, detail="cannot verify"),
    )
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: "declined_retry")
    deferred: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", deferred.append)
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "capture_github_login_skipped", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert deferred == [True]


def test_orchestrator_forced_cancel_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "forced")

    def _raise_cancel(**_kwargs: object) -> GitHubLoginResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(github_login_mod, "authenticate_and_configure_github", _raise_cancel)
    abandoned: list[tuple[str, str]] = []
    deferred: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", deferred.append)
    monkeypatch.setattr(
        flg,
        "capture_github_login_abandoned",
        lambda *, variant, reason: abandoned.append((variant, reason)),
    )
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is False
    assert deferred == []
    assert abandoned == [("forced", "cancelled")]


def test_orchestrator_forced_decline_retry_aborts_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "forced")
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=False, detail="cannot verify"),
    )
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: "declined_retry")
    abandoned: list[tuple[str, str]] = []
    deferred: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", deferred.append)
    monkeypatch.setattr(
        flg,
        "capture_github_login_abandoned",
        lambda *, variant, reason: abandoned.append((variant, reason)),
    )
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is False
    assert deferred == []
    assert abandoned == [("forced", "declined_retry")]


def test_orchestrator_forced_cancel_retry_aborts_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "forced")
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=False, detail="cannot verify"),
    )
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: "cancelled")
    abandoned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        flg,
        "capture_github_login_abandoned",
        lambda *, variant, reason: abandoned.append((variant, reason)),
    )
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is False
    assert abandoned == [("forced", "cancelled")]


def test_orchestrator_forced_success_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "forced")
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=True, username="octocat", detail="OK"),
    )
    completed: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        flg,
        "capture_github_login_completed",
        lambda username, *, variant=None: completed.append((username, variant)),
    )
    monkeypatch.setattr(flg, "clear_github_login_deferral", lambda: None)
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert completed == [("octocat", "forced")]


def test_orchestrator_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_GITHUB_GATE_VARIANT", "control")
    monkeypatch.setattr(flg, "_offer_github_login", lambda _console, **_kwargs: True)
    calls = {"n": 0}

    def _login(**_kwargs: object) -> GitHubLoginResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return GitHubLoginResult(ok=False, detail="cannot verify")
        return GitHubLoginResult(ok=True, username="octocat", detail="OK")

    monkeypatch.setattr(github_login_mod, "authenticate_and_configure_github", _login)
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: "retry")
    monkeypatch.setattr(flg, "capture_github_login_completed", lambda _username, **_kwargs: None)
    monkeypatch.setattr(flg, "clear_github_login_deferral", lambda: None)
    monkeypatch.setattr(flg, "capture_github_login_prompted", lambda **_kwargs: None)
    monkeypatch.setattr(flg, "stamp_github_gate_variant", lambda _variant: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert calls["n"] == 2


def test_clear_github_login_deferral_noop_when_not_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(flg, "read_github_login_deferred", lambda: False)
    writes: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", writes.append)

    flg.clear_github_login_deferral()

    assert writes == []


def test_clear_github_login_deferral_clears_when_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flg, "read_github_login_deferred", lambda: True)
    writes: list[bool] = []
    monkeypatch.setattr(flg, "write_github_login_deferred", writes.append)

    flg.clear_github_login_deferral()

    assert writes == [False]


def test_sleep_until_or_cancel_raises_on_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flg.os, "name", "posix")
    monkeypatch.setattr(flg.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(flg.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.components.key_reader.read_key_unix",
        lambda **_kwargs: "cancel",
    )
    monkeypatch.setattr("termios.tcgetattr", lambda _fd: [0] * 7)
    monkeypatch.setattr("tty.setraw", lambda _fd: None)
    restored: list[bool] = []
    monkeypatch.setattr(
        "termios.tcsetattr",
        lambda _fd, _when, _attrs: restored.append(True),
    )

    def _ready(
        _fd: int, *_args: object, **_kwargs: object
    ) -> tuple[list[int], list[int], list[int]]:
        return ([0], [], [])

    monkeypatch.setattr("select.select", _ready)

    with pytest.raises(KeyboardInterrupt):
        flg._sleep_until_or_cancel(1.0)

    assert restored == [True]
