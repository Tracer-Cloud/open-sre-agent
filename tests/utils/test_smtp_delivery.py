"""Tests for app/utils/smtp_delivery.py."""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.utils.smtp_delivery import (
    format_background_rca_email,
    send_smtp_report,
    verify_smtp_connection,
)


class _FakeSMTP:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        self.quit_called = False

    def ehlo(self) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def noop(self) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def quit(self) -> None:
        self.quit_called = True

    def close(self) -> None:
        self.quit_called = True


class _FailingLoginSMTP(_FakeSMTP):
    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)
        raise RuntimeError("auth failed")


def _return_fake_client(fake_client: _FakeSMTP):
    def _factory(*_args: object, **_kwargs: object) -> _FakeSMTP:
        return fake_client

    return _factory


def test_format_background_rca_email_includes_required_sections() -> None:
    subject, body = format_background_rca_email(
        task_id="bg-123",
        command="/investigate checkout",
        root_cause="postgres connection pool saturation",
        top_analysis=("rds cpu spike", "error rate climbed"),
        next_steps=("raise pool size",),
        stats={"tool_call_count": 4, "investigation_loop_count": 2, "validity_score": 0.91},
    )

    assert subject == "OpenSRE RCA complete: bg-123"
    assert "Root cause" in body
    assert "Top analysis" in body
    assert "What to do next" in body
    assert "Internal stats" in body


def test_verify_smtp_connection_uses_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeSMTP("smtp.example.com", 587, 15)
    monkeypatch.setattr("app.utils.smtp_delivery.smtplib.SMTP", _return_fake_client(fake_client))

    ok, detail = verify_smtp_connection(
        {
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "username": "mailer",
            "password": "secret",
        }
    )

    assert ok is True
    assert "successfully" in detail.lower()
    assert fake_client.started_tls is True
    assert fake_client.logged_in == ("mailer", "secret")


def test_send_smtp_report_sends_plain_text_email(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeSMTP("smtp.example.com", 465, 15)
    monkeypatch.setattr(
        "app.utils.smtp_delivery.smtplib.SMTP_SSL",
        _return_fake_client(fake_client),
    )

    ok, error = send_smtp_report(
        report="hello world",
        subject="RCA ready",
        smtp_ctx={
            "host": "smtp.example.com",
            "port": 465,
            "security": "ssl",
            "from_address": "opensre@example.com",
            "default_to": "team@example.com",
        },
    )

    assert ok is True
    assert error == ""
    assert len(fake_client.sent) == 1
    assert fake_client.sent[0]["Subject"] == "RCA ready"
    assert fake_client.sent[0]["From"] == "opensre@example.com"
    assert fake_client.sent[0]["To"] == "team@example.com"
    assert "hello world" in fake_client.sent[0].get_content()


def test_send_smtp_report_requires_recipient() -> None:
    ok, error = send_smtp_report(
        report="hello",
        subject="RCA ready",
        smtp_ctx={"from_address": "opensre@example.com"},
    )
    assert ok is False
    assert "recipient" in error.lower()


def test_verify_smtp_connection_closes_client_when_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FailingLoginSMTP("smtp.example.com", 587, 15)
    monkeypatch.setattr("app.utils.smtp_delivery.smtplib.SMTP", _return_fake_client(fake_client))

    ok, detail = verify_smtp_connection(
        {
            "host": "smtp.example.com",
            "port": 587,
            "security": "none",
            "username": "mailer",
            "password": "secret",
        }
    )

    assert ok is False
    assert "auth failed" in detail
    assert fake_client.quit_called is True
