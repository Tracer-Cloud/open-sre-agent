from __future__ import annotations

import subprocess
import sys

from platform.common.errors import OpenSREError
from surfaces.interactive_shell.runtime.background.notifications import (
    deliver_background_notifications,
)
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)


def test_deliver_background_notifications_sends_email_when_smtp_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "smtp": {
                "source": "local env",
                "config": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "security": "starttls",
                    "from_address": "opensre@example.com",
                    "default_to": "team@example.com",
                },
            }
        },
    )

    captured: dict[str, object] = {}

    def _fake_send_smtp_report(
        *, report: str, subject: str, smtp_ctx: dict[str, object]
    ) -> tuple[bool, str]:
        captured["report"] = report
        captured["subject"] = subject
        captured["smtp_ctx"] = smtp_ctx
        return True, ""

    monkeypatch.setattr(
        "integrations.smtp.delivery.send_smtp_report",
        _fake_send_smtp_report,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="/investigate checkout-latency",
        root_cause="postgres connection pool saturation",
        top_analysis=("rds cpu spike",),
        next_steps=("raise pool size",),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.8},
    )

    results = deliver_background_notifications(record=record, channels=("email",))

    assert results == {"email": "sent"}
    assert captured["subject"] == "OpenSRE RCA complete: bg-123"
    assert "Root cause" in str(captured["report"])


def test_deliver_background_notifications_skips_when_no_channels_configured() -> None:
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=())
    assert results == {}


def test_deliver_background_notifications_marks_missing_smtp(monkeypatch) -> None:
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", lambda: {})
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("email",))
    assert results == {"email": "missing smtp integration"}


# --- Telegram (Wave-2655) ---------------------------------------------------
#
# Patches below target the SOURCE modules
# (integrations.telegram.credentials.load_credentials_from_env /
# integrations.telegram.delivery.send_telegram_report), not the notifications
# module namespace: the implementation is required (AC-11) to lazily
# `from integrations.telegram.… import …` *inside* the branch, re-reading the
# attribute at call time, so a module-top / notifications-namespace patch
# would silently miss the real call.


def _stub_send_smtp_report_ok(
    *, report: str, subject: str, smtp_ctx: dict[str, object]
) -> tuple[bool, str]:
    """Shared no-op smtp stub for tests that only assert on the telegram result."""
    return True, ""


def test_deliver_background_notifications_sends_telegram_when_configured(
    monkeypatch,
) -> None:
    """AC-4 (+ AC-10 body reuse, AC-15 parse_mode): configured + send ok -> "sent"."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )

    captured: dict[str, object] = {}
    send_calls = 0

    def _fake_send_telegram_report(
        report: str, telegram_ctx: dict[str, object], *, parse_mode: str = "HTML", **_: object
    ) -> tuple[bool, str]:
        nonlocal send_calls
        send_calls += 1
        captured["report"] = report
        captured["telegram_ctx"] = telegram_ctx
        captured["parse_mode"] = parse_mode
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        _fake_send_telegram_report,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="/investigate checkout-latency",
        # Non-empty, distinctive sentinel (D2 hardening): "" in body is always
        # True, so a body-contains assertion against an empty root_cause would
        # be vacuous. This sentinel makes the assertion real.
        root_cause="ROOTSENTINEL postgres connection pool saturation",
        top_analysis=("TOPANALYSISSENTINEL rds cpu spike",),
        next_steps=("NEXTSTEPSENTINEL raise pool size",),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.8},
    )

    results = deliver_background_notifications(record=record, channels=("telegram",))

    assert results == {"telegram": "sent"}
    # A missed patch (module-top binding instead of the mandated lazy import)
    # must fail loudly here rather than silently reaching a real transport.
    assert send_calls == 1
    assert set(captured["telegram_ctx"].keys()) == {"bot_token", "chat_id"}
    assert captured["telegram_ctx"]["bot_token"] == "tok"
    assert captured["telegram_ctx"]["chat_id"] == "chat-1"
    assert captured["parse_mode"] == ""
    body = str(captured["report"])
    assert "ROOTSENTINEL" in body
    assert "TOPANALYSISSENTINEL" in body
    assert "NEXTSTEPSENTINEL" in body


def test_deliver_background_notifications_marks_telegram_failure(monkeypatch) -> None:
    """AC-5: configured + send fails with a non-empty error -> "failed: <error>"."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (False, "chat not found"),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results == {"telegram": "failed: chat not found"}


def test_deliver_background_notifications_marks_telegram_failure_with_empty_error(
    monkeypatch,
) -> None:
    """AC-28: empty error string from send -> "failed: " exactly (trailing space, no special-case)."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (False, ""),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results == {"telegram": "failed: "}


def test_deliver_background_notifications_marks_missing_telegram(monkeypatch) -> None:
    """AC-6: load_credentials_from_env raises OpenSREError -> graceful, detail-preserving message, no raise."""

    def _raise_missing(**_: object) -> None:
        raise OpenSREError(
            "TELEGRAM_BOT_TOKEN is not set.",
            suggestion=(
                "Configure Telegram with `opensre integrations setup telegram` "
                "(or `opensre onboard`), or export TELEGRAM_BOT_TOKEN=<your-bot-token>."
            ),
        )

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _raise_missing,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results["telegram"].startswith("missing telegram integration: ")
    assert "TELEGRAM_BOT_TOKEN is not set." in results["telegram"]


def test_deliver_background_notifications_marks_missing_telegram_for_blank_credentials(
    monkeypatch,
) -> None:
    """AC-25: blank (present-but-empty/whitespace) creds collapse to the same OpenSREError path as AC-6.

    Grounded: _resolve_bot_token/_resolve_chat_id .strip() blank values before
    the presence check (credentials.py:60,67,78,81), so load_credentials_from_env
    raises OpenSREError identically for absent and blank creds. This test mirrors
    that boundary by making the source function raise as it would for a blank
    chat id, and asserts the dispatcher routes it through the AC-6 guard.
    """

    def _raise_blank_chat_id(**_: object) -> None:
        raise OpenSREError(
            "Telegram chat id is not set.",
            suggestion=(
                "Set a default chat id during `opensre integrations setup telegram`, "
                "export TELEGRAM_DEFAULT_CHAT_ID=<chat-id>, or pass --chat-id and retry."
            ),
        )

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _raise_blank_chat_id,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results["telegram"].startswith("missing telegram integration: ")
    assert "Telegram chat id is not set." in results["telegram"]


def test_deliver_background_notifications_sends_email_and_telegram(monkeypatch) -> None:
    """AC-7: combined channels -> two independent keys, both "sent"."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "smtp": {
                "source": "local env",
                "config": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "security": "starttls",
                    "from_address": "opensre@example.com",
                    "default_to": "team@example.com",
                },
            }
        },
    )
    monkeypatch.setattr(
        "integrations.smtp.delivery.send_smtp_report",
        _stub_send_smtp_report_ok,
    )
    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (True, ""),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="free-text",
        root_cause="combined channel sentinel",
    )
    results = deliver_background_notifications(record=record, channels=("email", "telegram"))
    assert results == {"email": "sent", "telegram": "sent"}


def test_deliver_background_notifications_telegram_first_does_not_break_email(
    monkeypatch,
) -> None:
    """AC-12: telegram-first ordering with telegram unconfigured must not drop/blank email."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "smtp": {
                "source": "local env",
                "config": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "security": "starttls",
                    "from_address": "opensre@example.com",
                    "default_to": "team@example.com",
                },
            }
        },
    )
    monkeypatch.setattr(
        "integrations.smtp.delivery.send_smtp_report",
        _stub_send_smtp_report_ok,
    )

    def _raise_missing(**_: object) -> None:
        raise OpenSREError("TELEGRAM_BOT_TOKEN is not set.")

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _raise_missing,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("telegram", "email"))
    assert results["telegram"].startswith("missing telegram integration: ")
    assert "TELEGRAM_BOT_TOKEN is not set." in results["telegram"]
    assert results["email"] == "sent"


def test_deliver_background_notifications_dedupes_duplicate_telegram_channel(
    monkeypatch,
) -> None:
    """AC-27 (dispatcher layer): a duplicate "telegram" entry is last-write-wins into one key."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (True, ""),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="dup channel"
    )
    results = deliver_background_notifications(record=record, channels=("telegram", "telegram"))
    assert list(results.keys()) == ["telegram"]
    assert len(results) == 1
    assert results["telegram"] == "sent"


def test_deliver_background_notifications_telegram_empty_root_cause_still_sends(
    monkeypatch,
) -> None:
    """AC-24: empty root_cause/top_analysis/next_steps -> no crash, body renders "Unavailable", still sent."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )

    captured: dict[str, object] = {}
    send_calls = 0

    def _fake_send_telegram_report(
        report: str, telegram_ctx: dict[str, object], *, parse_mode: str = "HTML", **_: object
    ) -> tuple[bool, str]:
        nonlocal send_calls
        send_calls += 1
        captured["report"] = report
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        _fake_send_telegram_report,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="free-text",
        root_cause="",
        top_analysis=(),
        next_steps=(),
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))

    assert results == {"telegram": "sent"}
    assert send_calls == 1
    body = str(captured["report"])
    assert "Unavailable" in body
    assert body != ""


def test_deliver_background_notifications_telegram_body_passes_through_unescaped(
    monkeypatch,
) -> None:
    """AC-26 (Q3): unicode/emoji/angle-bracket body passes through unescaped with parse_mode=""."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )

    captured: dict[str, object] = {}

    def _fake_send_telegram_report(
        report: str, telegram_ctx: dict[str, object], *, parse_mode: str = "HTML", **_: object
    ) -> tuple[bool, str]:
        captured["report"] = report
        captured["parse_mode"] = parse_mode
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        _fake_send_telegram_report,
    )

    hostile = "boom <oom-killer> & 5 < 10 🔥❤️ café"
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause=hostile
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))

    assert results == {"telegram": "sent"}
    assert captured["parse_mode"] == ""
    body = str(captured["report"])
    assert hostile in body
    assert "&lt;" not in body
    assert "&amp;" not in body
    assert "&gt;" not in body


def test_notifications_module_does_not_eagerly_import_telegram() -> None:
    """AC-11 (corrected, dotted-module sys.modules check): telegram loads only when processed.

    Run in a fresh subprocess: sys.modules is process-global, so checking it in
    the current test process would be contaminated by whatever earlier tests in
    this session already imported. The banned/vacuous draft check used the
    non-dotted string 'telegram.delivery', which is never a real sys.modules
    key and so prints True unconditionally, regardless of implementation.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import surfaces.interactive_shell.runtime.background.notifications as n; "
                "import sys; "
                "assert 'integrations.telegram.delivery' not in sys.modules; "
                "assert 'integrations.telegram.credentials' not in sys.modules; "
                "print('OK: telegram not eagerly imported')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OK: telegram not eagerly imported" in completed.stdout


def test_deliver_background_notifications_email_only_never_touches_telegram_creds(
    monkeypatch,
) -> None:
    """AC-11 (unit companion): with channels=("email",), telegram creds resolution is never invoked."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "smtp": {
                "source": "local env",
                "config": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "security": "starttls",
                    "from_address": "opensre@example.com",
                    "default_to": "team@example.com",
                },
            }
        },
    )
    monkeypatch.setattr(
        "integrations.smtp.delivery.send_smtp_report",
        _stub_send_smtp_report_ok,
    )

    def _explode(**_: object) -> None:
        raise AssertionError("telegram creds must not be resolved for email-only channels")

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _explode,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="email only"
    )
    results = deliver_background_notifications(record=record, channels=("email",))
    assert results == {"email": "sent"}


def test_deliver_background_notifications_telegram_never_raises_on_expected_states(
    monkeypatch,
) -> None:
    """AC-13 (unit-level): send-failure and unconfigured paths both return normally, never raise."""
    from integrations.telegram.credentials import TelegramCredentials

    # Send-failure path: send_telegram_report returns (False, ...), never raises.
    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (False, "transport exploded"),
    )
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results == {"telegram": "failed: transport exploded"}

    # Unconfigured path: load_credentials_from_env raises OpenSREError, must be
    # caught internally rather than escaping to the caller.
    def _raise_missing(**_: object) -> None:
        raise OpenSREError("TELEGRAM_BOT_TOKEN is not set.")

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _raise_missing,
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results["telegram"].startswith("missing telegram integration: ")
    assert "TELEGRAM_BOT_TOKEN is not set." in results["telegram"]


def test_deliver_background_notifications_redacts_bot_token_from_telegram_failure(
    monkeypatch,
) -> None:
    """The bot token rides in the request URL, and the transport passes a non-JSON
    error body through verbatim. That string lands in the record and is rendered by
    `/background show`, so the token must never survive into the result."""
    from integrations.telegram.credentials import TelegramCredentials

    bot_token = "111222:HAPPYTOKEN"

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token=bot_token, chat_id="chat-1"),
    )
    # Mirrors the real leak path: an intercepting proxy returns a non-JSON body that
    # echoes the request URL, which post_telegram_message surfaces unredacted.
    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        lambda *_args, **_kwargs: (
            False,
            f"<html>502 Bad Gateway: https://api.telegram.org/bot{bot_token}/sendMessage</html>",
        ),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("telegram",))

    assert "HAPPYTOKEN" not in results["telegram"]
    assert bot_token not in results["telegram"]
    assert "<redacted>" in results["telegram"]
    # The rest of the diagnostic must survive; redaction is not error-swallowing.
    assert results["telegram"].startswith("failed: ")
    assert "502 Bad Gateway" in results["telegram"]


def test_deliver_background_notifications_telegram_body_keeps_actionable_tail(
    monkeypatch,
) -> None:
    """Telegram tail-truncates at 4096. The RCA body ends with "What to do next" and
    the stats block, so an unbounded root cause would push exactly the actionable
    sections off the end. The body must fit the cap with the tail intact."""
    from integrations.telegram.credentials import TelegramCredentials

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        lambda **_: TelegramCredentials(bot_token="tok", chat_id="chat-1"),
    )

    captured: dict[str, object] = {}

    def _fake_send_telegram_report(
        report: str, telegram_ctx: dict[str, object], *, parse_mode: str = "HTML", **_: object
    ) -> tuple[bool, str]:
        captured["report"] = report
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.delivery.send_telegram_report",
        _fake_send_telegram_report,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="/investigate " + "c" * 5_000,
        root_cause="r" * 6_000,
        top_analysis=tuple(f"analysis {i} " + "a" * 500 for i in range(12)),
        next_steps=tuple(f"NEXTSTEPSENTINEL{i} " + "n" * 500 for i in range(12)),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.8},
    )

    results = deliver_background_notifications(record=record, channels=("telegram",))
    assert results == {"telegram": "sent"}

    body = str(captured["report"])
    # Fits in one Telegram message without the transport having to amputate it.
    assert len(body) <= 4096
    # The sections that tell the on-call what to do are still there.
    assert "What to do next" in body
    assert "NEXTSTEPSENTINEL0" in body
    assert "Internal stats" in body
    assert "validity score" in body
    # Email keeps the full report; only the Telegram copy is budgeted.
    assert "r" * 1_000 not in body or len(body) <= 4096
