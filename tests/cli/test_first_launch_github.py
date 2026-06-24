from __future__ import annotations

import io

import pytest
from rich.console import Console

from app.cli import first_launch_github as flg
from app.cli.interactive_shell.ui.theme import DEVICE_CODE_ANSI
from app.integrations import github_login as github_login_mod
from app.integrations.github_login import GitHubLoginResult
from app.integrations.github_mcp_oauth import GitHubDeviceCode


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _terminal_console(output: io.StringIO) -> Console:
    return Console(file=output, force_terminal=True, color_system="truecolor", highlight=False)


def _force_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every gate input so that login would be required."""
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.setattr(flg, "_eligible_os", lambda: True)
    monkeypatch.setattr(flg, "is_test_run", lambda: False)
    monkeypatch.setattr(flg, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(flg, "_github_already_configured", lambda: False)


def test_gate_required_when_all_conditions_met(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    assert flg.should_require_github_login() is True


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_gate_skipped_by_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _force_required(monkeypatch)
    monkeypatch.setenv("OPENSRE_SKIP_GITHUB_LOGIN", value)
    assert flg.should_require_github_login() is False


def test_gate_skipped_on_ineligible_os(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "_eligible_os", lambda: False)
    assert flg.should_require_github_login() is False


def test_gate_skipped_in_test_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_required(monkeypatch)
    monkeypatch.setattr(flg, "is_test_run", lambda: True)
    assert flg.should_require_github_login() is False


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
    "system,expected",
    [("Darwin", True), ("Windows", True), ("Linux", False), ("FreeBSD", False)],
)
def test_eligible_os(monkeypatch: pytest.MonkeyPatch, system: str, expected: bool) -> None:
    monkeypatch.setattr(flg.platform, "system", lambda: system)
    assert flg._eligible_os() is expected


def test_device_code_prompt_highlights_user_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    output = io.StringIO()
    code = GitHubDeviceCode(
        device_code="dev-123",
        user_code="WXYZ-1234",
        verification_uri="https://github.com/login/device",
        expires_in=900,
        interval=5,
    )

    flg._show_device_code(_terminal_console(output), code)

    rendered = output.getvalue()
    assert f"{DEVICE_CODE_ANSI}WXYZ-1234" in rendered


def test_orchestrator_success_proceeds_and_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=True, username="octocat", detail="OK"),
    )
    identified: list[str] = []
    completed: list[str] = []
    monkeypatch.setattr(flg, "identify_github_username", identified.append)
    monkeypatch.setattr(flg, "capture_github_login_completed", completed.append)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert identified == ["octocat"]
    assert completed == ["octocat"]


def test_orchestrator_quit_does_not_proceed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_cancel(**_kwargs: object) -> GitHubLoginResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(github_login_mod, "authenticate_and_configure_github", _raise_cancel)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is False


def test_orchestrator_failure_then_decline_retry_quits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_login_mod,
        "authenticate_and_configure_github",
        lambda **_kwargs: GitHubLoginResult(ok=False, detail="cannot verify"),
    )
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: False)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is False


def test_orchestrator_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _login(**_kwargs: object) -> GitHubLoginResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return GitHubLoginResult(ok=False, detail="cannot verify")
        return GitHubLoginResult(ok=True, username="octocat", detail="OK")

    monkeypatch.setattr(github_login_mod, "authenticate_and_configure_github", _login)
    monkeypatch.setattr(flg, "_ask_retry", lambda _console: True)
    monkeypatch.setattr(flg, "identify_github_username", lambda _username: None)
    monkeypatch.setattr(flg, "capture_github_login_completed", lambda _username: None)

    proceed = flg.require_github_login_on_first_launch(_console())

    assert proceed is True
    assert calls["n"] == 2
