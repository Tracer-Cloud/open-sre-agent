from __future__ import annotations

import pytest

from app.utils.sentry_sdk import (
    _before_breadcrumb,
    _event_has_operator_actionable_llm_error,
    _is_sentry_disabled,
    _scrub_exception_value,
    _scrub_mapping_recursive,
    _scrub_request,
)


class TestIsSentryDisabled:
    def test_disabled_via_no_telemetry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")
        assert _is_sentry_disabled() is True

    def test_disabled_via_sentry_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_SENTRY_DISABLED", "1")
        assert _is_sentry_disabled() is True

    def test_disabled_via_do_not_track(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        assert _is_sentry_disabled() is True

    def test_enabled_when_no_env_vars_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
        monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)
        assert _is_sentry_disabled() is False


class TestScrubMappingRecursive:
    @pytest.mark.parametrize(
        "key",
        ["api_key", "auth_token", "db_password", "client_secret", "bearer", "dsn", "cookie"],
    )
    def test_redacts_sensitive_keys_in_place(self, key: str) -> None:
        data = {key: "super-secret"}
        _scrub_mapping_recursive(data)
        assert data[key] != "super-secret"

    def test_preserves_safe_keys(self) -> None:
        data = {"name": "opensre", "version": "0.1.0", "timeout": 30}
        _scrub_mapping_recursive(data)
        assert data == {"name": "opensre", "version": "0.1.0", "timeout": 30}

    def test_handles_nested_dict(self) -> None:
        data = {"config": {"api_key": "secret", "retries": 3}}
        _scrub_mapping_recursive(data)
        assert data["config"]["api_key"] != "secret"
        assert data["config"]["retries"] == 3

    def test_handles_empty_dict(self) -> None:
        data: dict = {}
        _scrub_mapping_recursive(data)
        assert data == {}

    def test_prompt_key_is_redacted(self) -> None:
        data = {"system_prompt": "You are an assistant"}
        _scrub_mapping_recursive(data)
        assert data["system_prompt"] != "You are an assistant"

    def test_sensitive_value_replaced_with_filtered(self) -> None:
        data = {"api_key": "my-secret-key"}
        _scrub_mapping_recursive(data)
        assert data["api_key"] == "[Filtered]"


class TestScrubExceptionValue:
    def test_strips_input_value_from_pydantic_error(self) -> None:
        text = "validation error: field required [input_value=secret-token, input_type=str]"
        result = _scrub_exception_value(text)
        assert "secret-token" not in result

    def test_strips_short_input_form_from_pydantic_error(self) -> None:
        text = "validation error: field required [input=secret-token, input_type=str]"
        result = _scrub_exception_value(text)
        assert "secret-token" not in result

    def test_returns_unchanged_text_without_input_value(self) -> None:
        text = "some generic error message"
        result = _scrub_exception_value(text)
        assert result == text

    def test_handles_empty_string(self) -> None:
        assert _scrub_exception_value("") == ""


class TestScrubRequest:
    def test_removes_sensitive_headers_in_place(self) -> None:
        request: dict = {"headers": {"Authorization": "Bearer secret", "Content-Type": "application/json"}}
        _scrub_request(request)
        assert request["headers"].get("Authorization") != "Bearer secret"

    def test_preserves_safe_headers(self) -> None:
        request: dict = {"headers": {"Content-Type": "application/json"}}
        _scrub_request(request)
        assert request["headers"]["Content-Type"] == "application/json"

    def test_handles_missing_headers(self) -> None:
        request: dict = {}
        _scrub_request(request)
        assert isinstance(request, dict)


class TestBeforeBreadcrumb:
    def test_returns_non_http_crumb_unchanged(self) -> None:
        crumb: dict = {"category": "log", "data": {"message": "something happened"}}
        result = _before_breadcrumb(crumb, {})
        assert result == crumb

    def test_strips_query_string_from_http_breadcrumb(self) -> None:
        crumb: dict = {"category": "http", "data": {"url": "https://api.example.com/v1?token=secret"}}
        result = _before_breadcrumb(crumb, {})
        assert result is not None
        assert result["data"]["url"] == "https://api.example.com/v1"

    def test_scrubs_sensitive_header_from_http_breadcrumb(self) -> None:
        crumb: dict = {"category": "http", "data": {"headers": {"Authorization": "Bearer secret"}}}
        result = _before_breadcrumb(crumb, {})
        assert result is not None
        assert result["data"]["headers"]["Authorization"] == "[Filtered]"

    def test_handles_empty_crumb(self) -> None:
        result = _before_breadcrumb({}, {})
        assert result is not None


class TestEventHasOperatorActionableLlmError:
    @pytest.mark.parametrize(
        "error_text",
        [
            "missing OPENAI_API_KEY",
            "requires ANTHROPIC_API_KEY to be set",
            "credit balance is too low",
            "billing quota exceeded",
            "requires a cross-region inference profile",
            "rate limit exceeded your quota",
        ],
    )
    def test_returns_true_for_actionable_errors(self, error_text: str) -> None:
        event = {"exception": {"values": [{"type": "LLMError", "value": error_text}]}}
        assert _event_has_operator_actionable_llm_error(event) is True

    def test_returns_false_for_generic_error(self) -> None:
        event = {"exception": {"values": [{"type": "RuntimeError", "value": "unexpected state at line 42"}]}}
        assert _event_has_operator_actionable_llm_error(event) is False

    def test_returns_false_for_empty_event(self) -> None:
        assert _event_has_operator_actionable_llm_error({}) is False
