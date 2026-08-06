"""Tests for the Buzz integration: config, classify, client, and verifier."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from integrations.buzz import classify
from integrations.buzz.client import BuzzClient, resolve_buzz_binary
from integrations.buzz.credentials import BuzzCredentials, load_credentials_from_env
from integrations.buzz.verifier import verify_buzz
from integrations.config_models import BuzzConfig
from platform.common.errors import OpenSREError

# ---------------------------------------------------------------------------
# BuzzConfig
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    cfg = BuzzConfig.model_validate({"private_key": "nsec1x"})
    assert cfg.relay_url == "http://localhost:3000"
    assert cfg.buzz_path == "buzz"


def test_config_strips_trailing_slash_from_relay_url() -> None:
    cfg = BuzzConfig.model_validate({"relay_url": "https://buzz.example.com/", "private_key": "k"})
    assert cfg.relay_url == "https://buzz.example.com"


def test_config_rejects_relay_url_without_scheme() -> None:
    with pytest.raises(ValidationError):
        BuzzConfig.model_validate({"relay_url": "buzz.example.com", "private_key": "k"})


def test_config_is_configured_reflects_private_key() -> None:
    assert BuzzConfig.model_validate({}).is_configured is False
    assert BuzzConfig.model_validate({"private_key": "nsec1x"}).is_configured is True


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_returns_config_for_valid_credentials() -> None:
    cfg, key = classify(
        {
            "private_key": "nsec1x",
            "relay_url": "https://buzz.example.com",
            "default_channel": "chan-uuid",
        },
        record_id="rec-1",
    )
    assert key == "buzz"
    assert cfg is not None
    assert cfg.private_key == "nsec1x"
    assert cfg.default_channel == "chan-uuid"


def test_classify_skips_when_private_key_missing() -> None:
    assert classify({"relay_url": "https://buzz.example.com"}, record_id="rec-1") == (None, None)


def test_classify_validation_error_returns_none_and_reports() -> None:
    secret_value = "leaked-secret-key"

    with patch("integrations._validation_helpers.report_exception") as mock_report:
        result = classify(
            {"private_key": secret_value, "relay_url": "not-a-url"},
            record_id="rec-buzz",
        )

    assert result == (None, None)
    assert mock_report.call_count == 1
    exc_arg = mock_report.call_args.args[0]
    assert not isinstance(exc_arg, ValidationError)
    assert str(exc_arg) == "buzz config validation failed"
    assert secret_value not in str(exc_arg)


# ---------------------------------------------------------------------------
# resolve_buzz_binary
# ---------------------------------------------------------------------------


def test_resolve_buzz_binary_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_binary = tmp_path / "buzz"
    fake_binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("BUZZ_PATH", str(fake_binary))
    assert resolve_buzz_binary("buzz") == str(fake_binary)


def test_resolve_buzz_binary_falls_back_to_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    assert resolve_buzz_binary("buzz") == "/usr/bin/buzz"


def test_resolve_buzz_binary_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: None)
    assert resolve_buzz_binary("buzz") is None


# ---------------------------------------------------------------------------
# BuzzClient.probe_access
# ---------------------------------------------------------------------------


def _client(**overrides: Any) -> BuzzClient:
    config = BuzzConfig.model_validate(
        {"private_key": "nsec1x", "relay_url": "http://localhost:3000", **overrides}
    )
    return BuzzClient(config)


def test_probe_missing_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    client = BuzzClient(BuzzConfig.model_validate({}))
    result = client.probe_access()
    assert result.status == "missing"
    assert "private_key" in result.detail


def test_probe_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: None)
    result = _client().probe_access()
    assert result.status == "missing"
    assert "not found" in result.detail.lower()


def _fake_run_factory(returncode: int, stdout: str = "", stderr: str = ""):
    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return fake_run


def test_probe_passes_and_reports_channel_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(0, stdout='[{"id": "c1"}, {"id": "c2"}]'),
    )
    result = _client().probe_access()
    assert result.ok is True
    assert "2 channel(s)" in result.detail


@pytest.mark.parametrize(
    "returncode,stderr,expected_snippet",
    [
        (3, '{"error": "auth", "message": "invalid key"}', "authentication failed"),
        (2, '{"error": "network", "message": "connection refused"}', "unreachable"),
        (4, "boom", "exit 4"),
    ],
)
def test_probe_reports_failure_detail(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: str, expected_snippet: str
) -> None:
    monkeypatch.delenv("BUZZ_PATH", raising=False)
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run", _fake_run_factory(returncode, stderr=stderr)
    )
    result = _client().probe_access()
    assert result.status == "failed"
    assert expected_snippet in result.detail.lower()


# ---------------------------------------------------------------------------
# BuzzClient.send_message
# ---------------------------------------------------------------------------


def test_send_message_requires_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    result = _client().send_message(channel="  ", content="hi")
    assert result["success"] is False
    assert "channel is required" in result["error"]


def test_send_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(0, stdout='{"event_id": "evt-1", "accepted": true, "message": "ok"}'),
    )
    result = _client().send_message(channel="chan-uuid", content="hello")
    assert result == {"success": True, "error": "", "event_id": "evt-1"}


def test_send_message_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(
            0, stdout='{"event_id": "evt-1", "accepted": false, "message": "superseded"}'
        ),
    )
    result = _client().send_message(channel="chan-uuid", content="hello")
    assert result["success"] is False
    assert result["error"] == "superseded"


def test_send_message_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run",
        _fake_run_factory(1, stderr='{"error": "user", "message": "channel not found"}'),
    )
    result = _client().send_message(channel="chan-uuid", content="hello")
    assert result["success"] is False
    assert result["error"] == "channel not found"


def test_send_message_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run", _fake_run_factory(0, stdout="not json")
    )
    result = _client().send_message(channel="chan-uuid", content="hello")
    assert result["success"] is False
    assert "invalid JSON" in result["error"]


def test_send_message_reply_to_appends_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0, stdout='{"event_id": "evt-1", "accepted": true}', stderr=""
        )

    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr("integrations.buzz.client.subprocess.run", fake_run)
    _client().send_message(channel="chan-uuid", content="hello", reply_to="evt-parent")
    assert "--reply-to" in captured["cmd"]
    assert "evt-parent" in captured["cmd"]


def test_private_key_never_in_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The private key is injected via env only — never appears in the subprocess argv."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return SimpleNamespace(
            returncode=0, stdout='{"event_id": "evt-1", "accepted": true}', stderr=""
        )

    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr("integrations.buzz.client.subprocess.run", fake_run)
    client = _client(private_key="super-secret-nsec")
    client.send_message(channel="chan-uuid", content="hello")
    assert "super-secret-nsec" not in captured["cmd"]
    assert captured["env"]["BUZZ_PRIVATE_KEY"] == "super-secret-nsec"


# ---------------------------------------------------------------------------
# verify_buzz
# ---------------------------------------------------------------------------


def test_verify_buzz_missing_when_unconfigured() -> None:
    result = verify_buzz("local env", {})
    assert result["status"] == "missing"


def test_verify_buzz_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.buzz.client.shutil.which", lambda _name: "/usr/bin/buzz")
    monkeypatch.setattr(
        "integrations.buzz.client.subprocess.run", _fake_run_factory(0, stdout="[]")
    )
    result = verify_buzz("local env", {"private_key": "nsec1x"})
    assert result["status"] == "passed"


# ---------------------------------------------------------------------------
# BuzzCredentials / load_credentials_from_env
# ---------------------------------------------------------------------------


def test_credentials_repr_hides_private_key_and_auth_tag() -> None:
    creds = BuzzCredentials(private_key="nsec1x", auth_tag='{"x":1}', channel="chan-uuid")
    rendered = repr(creds)
    assert "nsec1x" not in rendered
    assert '{"x":1}' not in rendered


def test_load_credentials_raises_when_no_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.scheduler.credentials.resolve_buzz_credentials", lambda _params: {}
    )
    with pytest.raises(OpenSREError, match="not configured"):
        load_credentials_from_env()


def test_load_credentials_raises_when_no_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.scheduler.credentials.resolve_buzz_credentials",
        lambda _params: {"private_key": "nsec1x"},
    )
    monkeypatch.setattr("integrations.buzz.credentials._store_default_channel", lambda: "")
    monkeypatch.delenv("BUZZ_DEFAULT_CHANNEL", raising=False)
    with pytest.raises(OpenSREError, match="no channel"):
        load_credentials_from_env()


def test_load_credentials_succeeds_with_key_and_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "platform.scheduler.credentials.resolve_buzz_credentials",
        lambda _params: {"private_key": "nsec1x", "relay_url": "http://localhost:3000"},
    )
    creds = load_credentials_from_env(channel_override="chan-uuid")
    assert creds.private_key == "nsec1x"
    assert creds.channel == "chan-uuid"
    assert creds.buzz_path == "buzz"


def test_load_credentials_preserves_custom_buzz_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a stored non-PATH buzz_path must survive into watchdog/alarm
    credential resolution, not silently fall back to a bare `buzz` PATH lookup."""
    monkeypatch.setattr(
        "platform.scheduler.credentials.resolve_buzz_credentials",
        lambda _params: {
            "private_key": "nsec1x",
            "relay_url": "http://localhost:3000",
            "buzz_path": "/opt/buzz/bin/buzz",
        },
    )
    creds = load_credentials_from_env(channel_override="chan-uuid")
    assert creds.buzz_path == "/opt/buzz/bin/buzz"


def test_resolve_buzz_credentials_includes_buzz_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform.scheduler.credentials import resolve_buzz_credentials

    monkeypatch.setattr(
        "platform.scheduler.credentials._get_integration_credential",
        lambda _service, key: "/opt/buzz/bin/buzz" if key == "buzz_path" else "",
    )
    resolved = resolve_buzz_credentials({})
    assert resolved.get("buzz_path") == "/opt/buzz/bin/buzz"
