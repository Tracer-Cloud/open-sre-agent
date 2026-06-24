from __future__ import annotations

from app.cli.interactive_shell.runtime.background import BackgroundInvestigationRecord
from app.cli.interactive_shell.runtime.background_notifications import (
    deliver_background_notifications,
)


def test_deliver_background_notifications_sends_email_when_smtp_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.resolve_effective_integrations",
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
        "app.cli.interactive_shell.runtime.background_notifications.send_smtp_report",
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
    monkeypatch.setattr("app.integrations.catalog.resolve_effective_integrations", lambda: {})
    record = BackgroundInvestigationRecord(
        task_id="bg-123", status="completed", command="free-text"
    )
    results = deliver_background_notifications(record=record, channels=("email",))
    assert results == {"email": "missing smtp integration"}
