"""Tests for integrations/buzz/delivery.py."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from integrations.buzz.delivery import post_buzz_message, send_buzz_report

_RELAY = "http://localhost:3000"
_PRIVATE_KEY = "super-secret-nsec"
_CTX = {
    "relay_url": _RELAY,
    "channel": "chan-uuid",
    "private_key": _PRIVATE_KEY,
    "auth_tag": "",
    "buzz_path": "buzz",
}


def _fake_run_factory(returncode: int, stdout: str = "", stderr: str = ""):
    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return fake_run


# ---------------------------------------------------------------------------
# post_buzz_message
# ---------------------------------------------------------------------------


def test_post_message_missing_binary_never_leaks_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: None)
    ok, error, _event_id = post_buzz_message(_RELAY, "chan-uuid", "hello", _PRIVATE_KEY)
    assert ok is False
    assert _PRIVATE_KEY not in error


def test_post_message_logs_never_contain_private_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(1, stderr=f'{{"error": "user", "message": "bad key {_PRIVATE_KEY}"}}'),
    )
    with caplog.at_level(logging.WARNING):
        post_buzz_message(_RELAY, "chan-uuid", "hello", _PRIVATE_KEY)
    assert _PRIVATE_KEY not in caplog.text


def test_post_message_surfaces_client_error_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """post_buzz_message is a thin passthrough; exhaustive exit-code -> error-string
    mapping is BuzzClient's concern and is covered in tests/integrations/test_buzz.py.
    This only confirms the wrapper doesn't swallow or reformat the detail."""
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(1, stderr='{"error": "user", "message": "channel not found"}'),
    )
    ok, error, event_id = post_buzz_message(_RELAY, "chan-uuid", "hello", _PRIVATE_KEY)
    assert ok is False
    assert error == "channel not found"
    assert event_id == ""


# ---------------------------------------------------------------------------
# send_buzz_report
# ---------------------------------------------------------------------------


def test_send_report_posts_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0, stdout='{"event_id": "evt-1", "accepted": true}', stderr=""
        )

    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr("integrations.buzz.client.subprocess.run", fake_run)
    ok, error = send_buzz_report("Report text", _CTX)
    assert ok is True
    assert error == ""
    content_index = captured["cmd"].index("--content") + 1
    assert "OpenSRE Investigation" in captured["cmd"][content_index]
    assert "Report text" in captured["cmd"][content_index]


def test_send_report_truncates_text_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0, stdout='{"event_id": "evt-1", "accepted": true}', stderr=""
        )

    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr("integrations.buzz.client.subprocess.run", fake_run)
    long_report = "x" * 5000
    send_buzz_report(long_report, _CTX)
    content_index = captured["cmd"].index("--content") + 1
    text = captured["cmd"][content_index]
    assert len(text) == 4096
    assert text.endswith("…")


def test_send_report_defaults_relay_and_buzz_path_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["env"] = kwargs.get("env", {})
        return SimpleNamespace(
            returncode=0, stdout='{"event_id": "evt-1", "accepted": true}', stderr=""
        )

    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr("integrations.buzz.client.subprocess.run", fake_run)
    minimal_ctx = {"channel": "chan-uuid", "private_key": _PRIVATE_KEY}
    ok, _error = send_buzz_report("Report", minimal_ctx)
    assert ok is True
    assert captured["env"]["BUZZ_RELAY_URL"] == "http://localhost:3000"
