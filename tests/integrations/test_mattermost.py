from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from integrations.config_models import MattermostConfig
from integrations.mattermost import classify
from integrations.mattermost.verifier import verify_mattermost

# ---------------------------------------------------------------------------
# MattermostConfig
# ---------------------------------------------------------------------------


def test_config_strips_trailing_slash_from_server_url() -> None:
    cfg = MattermostConfig.model_validate(
        {"server_url": "https://chat.example.com/", "auth_token": "tok"}
    )
    assert cfg.server_url == "https://chat.example.com"


def test_config_rejects_server_url_without_scheme() -> None:
    with pytest.raises(ValidationError):
        MattermostConfig.model_validate({"server_url": "chat.example.com", "auth_token": "tok"})


@pytest.mark.parametrize("field", ["server_url", "auth_token"])
def test_config_rejects_blank_pat_fields_without_webhook(field: str) -> None:
    payload = {
        "server_url": "https://chat.example.com",
        "auth_token": "tok",
    }
    payload[field] = "   "
    with pytest.raises(ValidationError):
        MattermostConfig.model_validate(payload)


def test_config_accepts_webhook_only() -> None:
    cfg = MattermostConfig.model_validate({"webhook_url": "https://chat.example.com/hooks/abc"})
    assert cfg.webhook_url == "https://chat.example.com/hooks/abc"
    assert cfg.auth_token == ""


def test_config_accepts_incomplete_pat_when_webhook_present() -> None:
    cfg = MattermostConfig.model_validate(
        {"webhook_url": "https://chat.example.com/hooks/abc", "auth_token": "tok"}
    )
    assert cfg.webhook_url


def test_config_rejects_invalid_webhook_url() -> None:
    with pytest.raises(ValidationError):
        MattermostConfig.model_validate({"webhook_url": "chat.example.com/hooks/abc"})


def test_config_accepts_both_modes() -> None:
    cfg = MattermostConfig.model_validate(
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "webhook_url": "https://chat.example.com/hooks/abc",
        }
    )
    assert cfg.server_url and cfg.webhook_url


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_returns_config_for_valid_credentials() -> None:
    cfg, key = classify(
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "default_channel": "chan-id",
        },
        record_id="rec-1",
    )
    assert key == "mattermost"
    assert cfg is not None
    assert cfg.server_url == "https://chat.example.com"
    assert cfg.default_channel == "chan-id"


def test_classify_skips_when_auth_token_missing() -> None:
    assert classify({"server_url": "https://chat.example.com"}, record_id="rec-1") == (None, None)


def test_classify_accepts_webhook_only_credentials() -> None:
    cfg, key = classify(
        {"webhook_url": "https://chat.example.com/hooks/abc"},
        record_id="rec-1",
    )
    assert key == "mattermost"
    assert cfg is not None
    assert cfg.webhook_url == "https://chat.example.com/hooks/abc"


def test_classify_validation_error_returns_none_and_reports() -> None:
    """A ValidationError in classify() returns (None, None) and reports a
    sanitized wrapper (no secret field values), mirroring the Discord rule."""
    secret_value = "leaked-secret-token"

    with patch("integrations._validation_helpers.report_exception") as mock_report:
        result = classify(
            {"auth_token": secret_value, "server_url": "not-a-url"},
            record_id="rec-mattermost",
        )

    assert result == (None, None)
    assert mock_report.call_count == 1
    exc_arg = mock_report.call_args.args[0]
    assert not isinstance(exc_arg, ValidationError)
    assert str(exc_arg) == "mattermost config validation failed"
    assert secret_value not in str(exc_arg)


# ---------------------------------------------------------------------------
# verify_mattermost
# ---------------------------------------------------------------------------

_VALID_CONFIG = {
    "server_url": "https://chat.example.com",
    "auth_token": "tok",
    "default_channel": "chan-1",
}


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def test_verify_missing_server_url() -> None:
    result = verify_mattermost("local env", {"auth_token": "tok"})
    assert result["status"] == "missing"
    assert "server_url" in result["detail"]


def test_verify_missing_auth_token() -> None:
    result = verify_mattermost("local env", {"server_url": "https://chat.example.com"})
    assert result["status"] == "missing"
    assert "auth_token" in result["detail"]


def test_verify_passes_and_reports_username(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int) -> MagicMock:
        captured["url"] = url
        captured["headers"] = headers
        return _mock_response(200, {"username": "opensre.bot"})

    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", _fake_get)
    result = verify_mattermost("local env", _VALID_CONFIG)

    assert result["status"] == "passed"
    assert "@opensre.bot" in result["detail"]
    assert captured["url"] == "https://chat.example.com/api/v4/users/me"
    assert captured["headers"] == {"Authorization": "Bearer tok"}


def test_verify_token_without_channel_is_missing_not_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth alone must not report "passed": every unattended delivery path

    (investigation reports, watchdog alarms, background notifications) needs
    a channel once token credentials are configured, and (per delivery.py's
    routing rule) a configured webhook does not rescue a channel-less token
    setup — token credentials refuse delivery rather than silently falling
    back. Reporting "passed" here would hide that every automatic delivery
    will fail.
    """
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(200, {"username": "opensre.bot"}),
    )
    config_without_channel = {
        "server_url": "https://chat.example.com",
        "auth_token": "tok",
    }
    result = verify_mattermost("local env", config_without_channel)

    assert result["status"] == "missing"
    assert "@opensre.bot" in result["detail"]
    assert "default_channel" in result["detail"]


def test_verify_token_without_channel_is_missing_when_key_present_but_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``default_channel`` present-but-``None`` must be treated
    the same as absent.

    This is the *real* production shape, not just a hand-built test dict:
    ``MattermostConfig.default_channel: str | None = None`` means
    ``MattermostConfig.model_dump()`` — what ``resolve_effective_integrations()``
    actually returns — always includes the key, set to ``None`` when unconfigured.
    ``config.get("default_channel", "")`` would silently miss this, since the
    fallback only applies when the key is *absent*; ``str(None)`` is the
    truthy string ``"None"``, which would defeat the missing-channel check
    entirely on every real call through ``opensre integrations verify
    mattermost`` while still passing on a hand-built dict that omits the key.
    """
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(200, {"username": "opensre.bot"}),
    )
    config = {
        "server_url": "https://chat.example.com",
        "auth_token": "tok",
        "webhook_url": "",
        "default_channel": None,
    }
    result = verify_mattermost("local env", config)

    assert result["status"] == "missing"
    assert "default_channel" in result["detail"]


def test_verify_token_without_channel_is_missing_even_with_webhook_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A webhook does not rescue a channel-less token setup.

    delivery.py's send_mattermost_report() refuses to fall back to the
    webhook once token credentials are present — so a dual-mode config with
    no channel is just as broken for delivery as token-only, and the
    verifier must flag it the same way.
    """
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(200, {"username": "opensre.bot"}),
    )
    config = {
        "server_url": "https://chat.example.com",
        "auth_token": "tok",
        "webhook_url": "https://chat.example.com/hooks/abc",
    }
    result = verify_mattermost("local env", config)

    assert result["status"] == "missing"


def test_verify_reports_invalid_credentials_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(401, {"message": "Unauthorized"}),
    )
    result = verify_mattermost("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "invalid or expired" in result["detail"]


def test_verify_reports_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(503, {}),
    )
    result = verify_mattermost("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "HTTP 503" in result["detail"]


def test_verify_reports_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", _raise)
    result = verify_mattermost("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "connection refused" in result["detail"]


def test_verify_passes_with_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", lambda *_a, **_kw: resp)
    result = verify_mattermost("local env", _VALID_CONFIG)
    assert result["status"] == "passed"
    assert "@unknown" in result["detail"]


# ---------------------------------------------------------------------------
# verify_mattermost — webhook mode
# ---------------------------------------------------------------------------

_WEBHOOK_CONFIG = {"webhook_url": "https://chat.example.com/hooks/abc"}


@pytest.mark.parametrize("status_code", [200, 400, 403, 405])
def test_verify_webhook_only_passes_on_reachable_probe(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(status_code, {})

    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", _fake_get)
    result = verify_mattermost("local env", _WEBHOOK_CONFIG)

    assert result["status"] == "passed"
    assert "non-posting probe" in result["detail"]
    assert captured["url"] == _WEBHOOK_CONFIG["webhook_url"]


def test_verify_webhook_fails_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.mattermost.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(404, {}),
    )
    result = verify_mattermost("local env", _WEBHOOK_CONFIG)
    assert result["status"] == "failed"
    assert "404" in result["detail"]


def test_verify_webhook_fails_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", _raise)
    result = verify_mattermost("local env", _WEBHOOK_CONFIG)
    assert result["status"] == "failed"
    assert "unreachable" in result["detail"]


def test_verify_prefers_pat_probe_when_both_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(200, {"username": "opensre.bot"})

    monkeypatch.setattr("integrations.mattermost.verifier.httpx.get", _fake_get)
    result = verify_mattermost("local env", {**_VALID_CONFIG, **_WEBHOOK_CONFIG})

    assert result["status"] == "passed"
    assert captured["url"] == "https://chat.example.com/api/v4/users/me"
