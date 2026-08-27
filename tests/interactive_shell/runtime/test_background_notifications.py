from __future__ import annotations

import subprocess
import sys

from infrastructure.errors import OpenSREError
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


def test_deliver_background_notifications_keeps_the_smtp_failure_class(monkeypatch) -> None:
    """The persisted outcome carries the exception class and nothing more.

    ``send_smtp_report`` already narrows every failure to ``type(exc).__name__``,
    so the class name is the whole error value — keeping it is what lets the
    local ``/background show`` table separate an auth failure from a connection
    one. Redaction for a chat transport happens at that sink, not here.
    """
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "smtp": {
                "config": {
                    "host": "smtp.example.com",
                    "from_address": "opensre@example.com",
                    "default_to": "team@example.com",
                },
            }
        },
    )

    def _refuse(**_kwargs: object) -> tuple[bool, str]:
        return False, "SMTPAuthenticationError"

    monkeypatch.setattr("integrations.smtp.delivery.send_smtp_report", _refuse)

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )

    results = deliver_background_notifications(record=record, channels=("email",))

    assert results == {"email": "failed: SMTPAuthenticationError"}


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
                "or export TELEGRAM_BOT_TOKEN=<your-bot-token>."
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


# --- Rocket.Chat -------------------------------------------------------------
#
# Same patching rule as the telegram cases: patch the SOURCE modules, because
# the implementation lazily imports inside the branch.

_ROCKETCHAT_PAT_ENTRY = {
    "rocketchat": {
        "source": "local env",
        "config": {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "user_id": "u1",
            "default_channel": "#incidents",
            "webhook_url": "",
        },
    }
}

_ROCKETCHAT_WEBHOOK_ENTRY = {
    "rocketchat": {
        "source": "local env",
        "config": {
            "server_url": "",
            "auth_token": "",
            "user_id": "",
            "default_channel": None,
            "webhook_url": "https://chat.example.com/hooks/abc/def",
        },
    }
}


def test_deliver_background_notifications_sends_rocketchat_via_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: dict(_ROCKETCHAT_PAT_ENTRY),
    )

    captured: dict[str, object] = {}

    def _fake_post(
        server_url: str, channel: str, text: str, auth_token: str, user_id: str
    ) -> tuple[bool, str, str]:
        captured.update(server_url=server_url, channel=channel, text=text)
        return True, "", "m-1"

    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_rocketchat_message",
        _fake_post,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="/investigate checkout-latency",
        root_cause="ROOTSENTINEL postgres connection pool saturation",
        top_analysis=("TOPANALYSISSENTINEL rds cpu spike",),
        next_steps=("NEXTSTEPSENTINEL raise pool size",),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.8},
    )

    results = deliver_background_notifications(record=record, channels=("rocketchat",))

    assert results == {"rocketchat": "sent"}
    assert captured["server_url"] == "https://chat.example.com"
    assert captured["channel"] == "#incidents"
    body = str(captured["text"])
    assert "ROOTSENTINEL" in body
    assert "NEXTSTEPSENTINEL" in body


def test_deliver_background_notifications_sends_rocketchat_via_webhook_when_no_pat(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: dict(_ROCKETCHAT_WEBHOOK_ENTRY),
    )

    captured: dict[str, object] = {}

    def _fake_webhook(webhook_url: str, text: str) -> tuple[bool, str]:
        captured.update(webhook_url=webhook_url, text=text)
        return True, ""

    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_rocketchat_webhook",
        _fake_webhook,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("rocketchat",))

    assert results == {"rocketchat": "sent"}
    assert captured["webhook_url"] == "https://chat.example.com/hooks/abc/def"


def test_deliver_background_notifications_marks_missing_rocketchat(monkeypatch) -> None:
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", lambda: {})
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("rocketchat",))
    assert results["rocketchat"].startswith("missing rocketchat integration: ")


def test_deliver_background_notifications_marks_rocketchat_missing_channel(
    monkeypatch,
) -> None:
    """Token credentials without a default_channel cannot pick a destination."""
    entry = {
        "rocketchat": {
            "source": "local env",
            "config": {
                **_ROCKETCHAT_PAT_ENTRY["rocketchat"]["config"],
                "default_channel": None,
            },
        }
    }
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: entry,
    )
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("rocketchat",))
    assert results["rocketchat"].startswith("missing rocketchat integration: ")
    assert "default_channel" in results["rocketchat"]


def test_deliver_background_notifications_pat_without_channel_never_falls_back_to_webhook(
    monkeypatch,
) -> None:
    """Mixed config: full token credentials + webhook + no default_channel.

    Token credentials mean channel-targeting mode, so the missing
    default_channel is surfaced as a configuration gap — the webhook's fixed
    destination is deliberately NOT used as a silent fallback (same routing
    rule as the rocketchat_send_message tool and the cron provider).
    """
    entry = {
        "rocketchat": {
            "source": "local env",
            "config": {
                **_ROCKETCHAT_PAT_ENTRY["rocketchat"]["config"],
                "default_channel": None,
                "webhook_url": "https://chat.example.com/hooks/abc/def",
            },
        }
    }
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: entry,
    )

    def _explode_webhook(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("webhook must not be used as a fallback when PAT is configured")

    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_rocketchat_webhook",
        _explode_webhook,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("rocketchat",))
    assert results["rocketchat"].startswith("missing rocketchat integration: ")
    assert "default_channel" in results["rocketchat"]


def test_deliver_background_notifications_redacts_rocketchat_token_from_failure(
    monkeypatch,
) -> None:
    """Failure detail lands in the record and `/background show` — the token must
    never survive into the result, mirroring the telegram redaction contract."""
    auth_token = "RC-HAPPYTOKEN"
    entry = {
        "rocketchat": {
            "source": "local env",
            "config": {
                **_ROCKETCHAT_PAT_ENTRY["rocketchat"]["config"],
                "auth_token": auth_token,
            },
        }
    }
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: entry,
    )
    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_rocketchat_message",
        lambda *_args, **_kwargs: (False, f"502 Bad Gateway echoing {auth_token}", ""),
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("rocketchat",))

    assert auth_token not in results["rocketchat"]
    assert "<redacted>" in results["rocketchat"]
    assert results["rocketchat"].startswith("failed: ")
    assert "502 Bad Gateway" in results["rocketchat"]


def test_deliver_background_notifications_rocketchat_body_keeps_actionable_tail(
    monkeypatch,
) -> None:
    """Same 4096 budget rule as telegram: the actionable tail must survive."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: dict(_ROCKETCHAT_PAT_ENTRY),
    )

    captured: dict[str, object] = {}

    def _fake_post(*args: object, **_kwargs: object) -> tuple[bool, str, str]:
        captured["text"] = args[2]
        return True, "", "m-1"

    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_rocketchat_message",
        _fake_post,
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

    results = deliver_background_notifications(record=record, channels=("rocketchat",))
    assert results == {"rocketchat": "sent"}

    body = str(captured["text"])
    assert len(body) <= 4096
    assert "What to do next" in body
    assert "NEXTSTEPSENTINEL0" in body


# Vendor transports that must never load just because the notification path was
# imported or its adapters were registered. Dotted names, because a bare
# "telegram.delivery" is never a real sys.modules key and would pass vacuously.
_VENDOR_TRANSPORTS = (
    "integrations.telegram.delivery",
    "integrations.telegram.credentials",
    "integrations.rocketchat.delivery",
    "integrations.buzz.delivery",
    "integrations.smtp.delivery",
    "integrations.catalog",
)

_ASSERT_NO_TRANSPORTS = "".join(
    f"assert {module!r} not in sys.modules, {module!r}; " for module in _VENDOR_TRANSPORTS
)

# Importing the REPL entry point must not pull any vendor client onto the boot path.
_SHIM_IMPORT_PROBE = (
    "import sys; "
    "import surfaces.interactive_shell.runtime.background.notifications; "
    f"{_ASSERT_NO_TRANSPORTS}"
    "print('OK: shim clean')"
)

# Stronger than the above: registering every adapter must also cost no transport.
# This is what fails if someone hoists a vendor import to an adapter's module level.
_BOOTSTRAP_PROBE = (
    "import sys; "
    "from bootstrap.adapters import install_notification_adapters; "
    "names = install_notification_adapters(); "
    "assert sorted(names) == ['buzz', 'email', 'rocketchat', 'telegram'], names; "
    f"{_ASSERT_NO_TRANSPORTS}"
    "print('OK: registration clean')"
)


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter.

    sys.modules is process-global, so checking it inside the test process would
    be contaminated by whatever earlier tests in the session already imported.
    """
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_notification_entry_point_does_not_eagerly_import_vendor_transports() -> None:
    """The REPL imports this module at boot; no vendor client may come with it."""
    completed = _run_probe(_SHIM_IMPORT_PROBE)

    assert completed.returncode == 0, completed.stderr
    assert "OK: shim clean" in completed.stdout


def test_registering_every_adapter_pulls_no_vendor_transport() -> None:
    """Registration is now all-or-nothing, so it must stay free.

    The old chain imported one channel module per requested channel, so an
    unused channel cost nothing by construction. The registry imports all four
    up front, which is only acceptable because each adapter keeps its vendor
    client inside the delivery function. Assert that rather than trust it: this
    is the test that fails if a future edit hoists an import to module level.
    """
    completed = _run_probe(_BOOTSTRAP_PROBE)

    assert completed.returncode == 0, completed.stderr
    assert "OK: registration clean" in completed.stdout


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


_BUZZ_ENTRY = {
    "buzz": {
        "source": "local env",
        "config": {
            "relay_url": "http://relay.example.com:3000",
            "private_key": "bz-priv-key",
            "auth_tag": "tag-1",
            "buzz_path": "buzz",
            "default_channel": "#incidents",
        },
    }
}


def test_deliver_background_notifications_sends_buzz(monkeypatch) -> None:
    """Buzz shipped in #4756 with no coverage; this pins the success path."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: dict(_BUZZ_ENTRY),
    )

    captured: dict[str, object] = {}

    def _fake_post(
        relay_url: str,
        channel: str,
        text: str,
        private_key: str,
        *,
        auth_tag: str = "",
        buzz_path: str = "buzz",
        reply_to: str = "",
    ) -> tuple[bool, str, str]:
        captured.update(
            relay_url=relay_url,
            channel=channel,
            text=text,
            private_key=private_key,
            auth_tag=auth_tag,
            buzz_path=buzz_path,
        )
        return True, "", "evt-1"

    monkeypatch.setattr("integrations.buzz.delivery.post_buzz_message", _fake_post)

    record = BackgroundInvestigationRecord(
        task_id="bg-123",
        status="completed",
        command="/investigate checkout-latency",
        root_cause="ROOTSENTINEL postgres connection pool saturation",
        top_analysis=("TOPANALYSISSENTINEL rds cpu spike",),
        next_steps=("NEXTSTEPSENTINEL raise pool size",),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.8},
    )

    results = deliver_background_notifications(record=record, channels=("buzz",))

    assert results == {"buzz": "sent"}
    assert captured["relay_url"] == "http://relay.example.com:3000"
    assert captured["channel"] == "#incidents"
    assert captured["auth_tag"] == "tag-1"
    body = str(captured["text"])
    assert "ROOTSENTINEL" in body
    assert "NEXTSTEPSENTINEL" in body


def test_deliver_background_notifications_buzz_missing_integration(monkeypatch) -> None:
    """No buzz entry at all."""
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", dict)

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("buzz",))

    assert results == {"buzz": "missing buzz integration: Buzz is not configured."}


def test_deliver_background_notifications_buzz_missing_private_key(monkeypatch) -> None:
    """A configured buzz entry with no private_key reports the same gap as no entry."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "buzz": {
                "source": "local env",
                "config": {"relay_url": "http://relay.example.com:3000", "private_key": ""},
            }
        },
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("buzz",))

    assert results == {"buzz": "missing buzz integration: Buzz is not configured."}


def test_deliver_background_notifications_buzz_missing_default_channel(monkeypatch) -> None:
    """A key without a destination is a configuration gap, named as such."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {
            "buzz": {
                "source": "local env",
                "config": {"private_key": "bz-priv-key", "default_channel": ""},
            }
        },
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("buzz",))

    assert results == {
        "buzz": (
            "missing buzz integration: no default_channel configured "
            "(set BUZZ_DEFAULT_CHANNEL or re-run setup)."
        )
    }


def test_deliver_background_notifications_buzz_redacts_private_key_on_failure(
    monkeypatch,
) -> None:
    """The failure string lands in the record and `/background show`, so the key
    must not survive into it even when the transport echoes it back."""
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: dict(_BUZZ_ENTRY),
    )

    def _fake_post(
        relay_url: str,
        channel: str,
        text: str,
        private_key: str,
        *,
        auth_tag: str = "",
        buzz_path: str = "buzz",
        reply_to: str = "",
    ) -> tuple[bool, str, str]:
        return False, f"relay rejected key {private_key}", ""

    monkeypatch.setattr("integrations.buzz.delivery.post_buzz_message", _fake_post)

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("buzz",))

    outcome = results["buzz"]
    assert outcome.startswith("failed: ")
    assert "bz-priv-key" not in outcome


def test_deliver_background_notifications_unknown_channel_is_unsupported(monkeypatch) -> None:
    """An unrecognised channel name reports 'unsupported' rather than raising."""
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", dict)

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )
    results = deliver_background_notifications(record=record, channels=("nope",))

    assert results == {"nope": "unsupported"}


def test_deliver_background_notifications_preserves_caller_channel_order(monkeypatch) -> None:
    """`/background show` renders `results` in insertion order, so the dispatcher
    must follow the caller's channel tuple. Dict equality cannot catch a reorder,
    so assert the key sequence explicitly."""
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", dict)

    def _raise_missing(**_: object) -> None:
        raise OpenSREError("TELEGRAM_BOT_TOKEN is not set.")

    monkeypatch.setattr(
        "integrations.telegram.credentials.load_credentials_from_env",
        _raise_missing,
    )

    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text", root_cause="boom"
    )

    forward = deliver_background_notifications(
        record=record, channels=("email", "telegram", "rocketchat", "buzz")
    )
    reverse = deliver_background_notifications(
        record=record, channels=("buzz", "rocketchat", "telegram", "email")
    )

    assert list(forward) == ["email", "telegram", "rocketchat", "buzz"]
    assert list(reverse) == ["buzz", "rocketchat", "telegram", "email"]
    # Same outcomes either way; only the ordering differs.
    assert forward == reverse
