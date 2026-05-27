from __future__ import annotations

import pytest

from app.utils.tool_trace import (
    format_json_preview,
    format_tool_trace_entry,
    redact_sensitive,
)


class TestRedactSensitive:
    @pytest.mark.parametrize(
        "key",
        ["api_key", "API_KEY", "token", "secret", "password", "credential"],
    )
    def test_redacts_sensitive_keys(self, key: str) -> None:
        result = redact_sensitive({key: "super-secret-value"})
        assert result[key] == "[redacted]"

    @pytest.mark.parametrize(
        "key",
        ["_private", "_internal"],
    )
    def test_marks_underscore_keys_as_runtime(self, key: str) -> None:
        result = redact_sensitive({key: object()})
        assert result[key] == "[runtime object]"

    @pytest.mark.parametrize(
        "key",
        ["llm_backend", "storage_backend"],
    )
    def test_marks_backend_keys_as_runtime(self, key: str) -> None:
        result = redact_sensitive({key: object()})
        assert result[key] == "[runtime object]"

    def test_preserves_safe_keys(self) -> None:
        result = redact_sensitive({"name": "summarizer", "version": 3})
        assert result == {"name": "summarizer", "version": 3}

    def test_handles_nested_dict(self) -> None:
        data = {"config": {"api_key": "secret", "timeout": 30}}
        result = redact_sensitive(data)
        assert result["config"]["api_key"] == "[redacted]"
        assert result["config"]["timeout"] == 30

    def test_handles_list(self) -> None:
        data = [{"api_key": "secret"}, {"name": "ok"}]
        result = redact_sensitive(data)
        assert result[0]["api_key"] == "[redacted]"
        assert result[1]["name"] == "ok"

    def test_handles_tuple(self) -> None:
        data = ({"password": "secret"},)
        result = redact_sensitive(data)
        assert result[0]["password"] == "[redacted]"

    def test_passthrough_for_non_container(self) -> None:
        assert redact_sensitive("plain string") == "plain string"
        assert redact_sensitive(42) == 42
        assert redact_sensitive(None) is None


class TestFormatJsonPreview:
    def test_returns_json_string(self) -> None:
        result = format_json_preview({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_truncates_long_output(self) -> None:
        large = {"data": "x" * 5000}
        result = format_json_preview(large, max_chars=100)
        assert result.endswith("... [truncated]")
        assert len(result) <= 100 + len("... [truncated]")

    def test_no_truncation_within_limit(self) -> None:
        small = {"key": "value"}
        result = format_json_preview(small, max_chars=4000)
        assert "... [truncated]" not in result

    def test_redacts_sensitive_data(self) -> None:
        result = format_json_preview({"api_key": "secret"})
        assert "secret" not in result
        assert "[redacted]" in result


class TestFormatToolTraceEntry:
    def test_includes_tool_name(self) -> None:
        entry = {"tool_name": "search", "loop_iteration": 1, "tool_args": {}, "data": "result"}
        result = format_tool_trace_entry(entry)
        assert "search" in result

    def test_falls_back_to_key_for_tool_name(self) -> None:
        entry = {"key": "fallback_tool", "loop_iteration": 1, "tool_args": {}, "data": "result"}
        result = format_tool_trace_entry(entry)
        assert "fallback_tool" in result

    def test_labels_iteration_minus_one_as_seed(self) -> None:
        entry = {"tool_name": "init", "loop_iteration": -1, "tool_args": {}, "data": "ok"}
        result = format_tool_trace_entry(entry)
        assert "seed" in result

    def test_includes_regular_iteration_number(self) -> None:
        entry = {"tool_name": "search", "loop_iteration": 3, "tool_args": {}, "data": "result"}
        result = format_tool_trace_entry(entry)
        assert "3" in result

    def test_truncates_long_output(self) -> None:
        entry = {"tool_name": "search", "loop_iteration": 1, "tool_args": {}, "data": "x" * 2000}
        result = format_tool_trace_entry(entry, max_output_chars=100)
        assert len(result) < 2000

    def test_redacts_sensitive_input(self) -> None:
        entry = {
            "tool_name": "search",
            "loop_iteration": 1,
            "tool_args": {"api_key": "secret"},
            "data": "result",
        }
        result = format_tool_trace_entry(entry)
        assert "secret" not in result
