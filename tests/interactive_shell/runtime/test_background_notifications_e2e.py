"""End-to-end tests for the background-investigation Telegram notification path.

These tests mock only the outermost HTTP transport (``httpx.post``) so the
real stack runs unmodified: the ``/background notify`` command surface, the
background runner's completion hook, the notification dispatcher, Telegram
credential resolution from the environment, and Telegram message delivery all
execute for real. Assertions are made against the exact JSON payload that
would be sent over the wire, and against the resulting investigation record.
"""

from __future__ import annotations

import io
import threading
from unittest.mock import MagicMock

import httpx
import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.runtime.background import runner as runner_mod
from surfaces.interactive_shell.runtime.background.runner import _start_background_investigation
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)


def _run_investigation_to_completion(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    final_state: dict[str, object],
) -> tuple[str, BackgroundInvestigationRecord]:
    """Drive the REAL runner completion hook (runner.py `_worker`) to completion.

    ``track_investigation`` is analytics infra, not part of the notify chain,
    so it is the one legitimate mock beyond the httpx wire (mirrors
    ``test_background_runner.py``). ``report_exception`` is captured so a
    delivery bug surfaces as a readable assertion instead of silent telemetry.
    """
    monkeypatch.setattr(
        runner_mod,
        "track_investigation",
        lambda **_kwargs: MagicMock(
            __enter__=MagicMock(return_value=MagicMock()),
            __exit__=MagicMock(return_value=False),
        ),
    )
    caught: dict[str, BaseException] = {}
    monkeypatch.setattr(
        runner_mod,
        "report_exception",
        lambda exc, **_kwargs: caught.setdefault("exc", exc),
    )

    def _fake_run(*, cancel_requested, **_kwargs: object) -> dict[str, object]:
        _ = cancel_requested
        return dict(final_state)

    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    task_id = _start_background_investigation(
        session=session,
        console=console,
        display_command="/investigate checkout-latency",
        run_fn=_fake_run,
        kwargs={},
    )
    # Join the real daemon worker so the completion hook has run to
    # completion before we inspect the record.
    for thread in threading.enumerate():
        if thread.name == f"background-investigation-{task_id}":
            thread.join(timeout=5.0)
            break
    record = session.terminal.background_investigations[task_id]
    assert "exc" not in caught, f"worker raised unexpectedly: {caught.get('exc')!r}"
    return task_id, record


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Install the httpx-boundary capture; return the captured-call dict.

    Defaults to a 200 OK Telegram response; tests that need a failure
    response overwrite ``httpx.post`` themselves after requesting this
    fixture (or skip it and install their own).
    """
    captured: dict[str, object] = {}

    def _fake_post(
        url: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        follow_redirects: bool = False,
        **_kwargs: object,
    ) -> httpx.Response:
        _ = (headers, follow_redirects)
        captured["url"] = url
        captured["payload"] = json
        captured["timeout"] = timeout
        return httpx.Response(status_code=200, json={"ok": True, "result": {"message_id": 4242}})

    monkeypatch.setattr(httpx, "post", _fake_post)
    return captured


def _neutralize_integration_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force credential resolution to fall through to the environment.

    With the store empty, ``load_credentials_from_env`` -> ``_resolve_bot_token``
    -> ``config.llm_credentials.resolve_env_credential`` checks the environment
    first and returns immediately when it is set, so a configured env var can
    never fall through to the system keyring.
    """
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", lambda: {})


def test_e2e_happy_path_full_chain(
    monkeypatch: pytest.MonkeyPatch, wire: dict[str, object]
) -> None:
    """Real runner -> dispatcher -> env credentials -> delivery, mocking only httpx.post."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111222:HAPPYTOKEN")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "-1009999")
    _neutralize_integration_store(monkeypatch)

    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    # (1) REAL command surface sets the channel.
    assert dispatch_slash("/background notify set telegram", session, console) is True
    assert session.terminal.background_notification_preferences.channels == ("telegram",)

    # (2) REAL runner completes a synthetic investigation and fires the real
    # completion hook, which fans out to the real dispatcher.
    final_state = {
        "root_cause": "ROOTSENTINEL postgres connection pool saturation",
        "validated_claims": [{"claim": "TOPCLAIM rds cpu spike to 98%"}],
        "remediation_steps": ["NEXTSTEP raise pool max to 40"],
        "evidence_entries": [{"id": 1}, {"id": 2}],
        "investigation_loop_count": 2,
        "validity_score": 0.8,
    }
    _task_id, record = _run_investigation_to_completion(session, monkeypatch, final_state)

    # (3) Runner recorded a real completion and a real "sent" result.
    assert record.status == "completed"
    assert record.notification_results == {"telegram": "sent"}

    # (4) The HTTP-boundary payload is exactly what Telegram would receive.
    payload = wire["payload"]
    assert isinstance(payload, dict)
    assert wire["url"] == "https://api.telegram.org/bot111222:HAPPYTOKEN/sendMessage"
    assert wire["timeout"] == 15.0
    assert payload["chat_id"] == "-1009999"
    assert "parse_mode" not in payload
    assert set(payload.keys()) == {"chat_id", "text"}
    text = payload["text"]
    assert "ROOTSENTINEL postgres connection pool saturation" in text
    assert "TOPCLAIM rds cpu spike to 98%" in text
    assert "NEXTSTEP raise pool max to 40" in text
    assert "Task ID:" in text
    # Negative gate: the bot token must never leak into the sent text.
    assert "HAPPYTOKEN" not in text


def test_e2e_failure_400_renders_in_background_show(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Telegram 400 propagates verbatim into the record and `/background show`."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111222:FAILTOKEN")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "-1000001")
    _neutralize_integration_store(monkeypatch)

    captured: dict[str, object] = {}

    def _fake_post(
        url: str,
        *,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        follow_redirects: bool = False,
        **_kwargs: object,
    ) -> httpx.Response:
        _ = (url, headers, timeout, follow_redirects)
        captured["payload"] = json
        return httpx.Response(
            status_code=400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
        )

    monkeypatch.setattr(httpx, "post", _fake_post)

    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    assert dispatch_slash("/background notify set telegram", session, console) is True

    final_state: dict[str, object] = {
        "root_cause": "kafka rebalance storm",
        "remediation_steps": ["pin partitions"],
    }
    task_id, record = _run_investigation_to_completion(session, monkeypatch, final_state)

    # Runner still marks the investigation completed despite the 400.
    assert record.status == "completed"
    # Provider error text preserved verbatim behind the "failed: " prefix.
    assert record.notification_results == {"telegram": "failed: Bad Request: chat not found"}
    # The wire still carried the plain-text payload (no parse_mode) on the 400 path.
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "parse_mode" not in payload

    # REAL `/background show` renders the failed row.
    show_console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    assert dispatch_slash(f"/background show {task_id}", session, show_console) is True
    out = show_console.file.getvalue()
    assert "telegram:failed: Bad Request: chat not found" in out
    assert "MarkupError" not in out
