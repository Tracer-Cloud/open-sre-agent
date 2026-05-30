"""Tests for tool-call trace formatting and redaction helpers."""

from __future__ import annotations

import pytest

from app.utils.tool_trace import (
    format_json_preview,
    format_tool_trace_entry,
    redact_sensitive,
)


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "api-key",
        "apiKey",
        "token",
        "secret",
        "password",
        "credential",
        "authorization",
        "auth_header",
    ],
)
def test_redact_sensitive_replaces_sensitive_keys(key: str) -> None:
    payload = {key: "super-secret", "safe": "visible"}
    assert redact_sensitive(payload) == {key: "[redacted]", "safe": "visible"}


@pytest.mark.parametrize(
    "key",
    [
        "_internal",
        "backend",
        "mcp_backend",
        "auth_backend",
    ],
)
def test_redact_sensitive_replaces_runtime_keys(key: str) -> None:
    payload = {key: {"client": "live"}, "safe": "visible"}
    assert redact_sensitive(payload) == {key: "[runtime object]", "safe": "visible"}


def test_redact_sensitive_recurses_into_nested_collections() -> None:
    payload = {
        "nested": {"api_key": "hidden"},
        "items": [{"token": "t1"}, {"safe": "ok"}],
        "tuple": ({"password": "p1"},),
    }
    assert redact_sensitive(payload) == {
        "nested": {"api_key": "[redacted]"},
        "items": [{"token": "[redacted]"}, {"safe": "ok"}],
        "tuple": [{"password": "[redacted]"}],
    }


@pytest.mark.parametrize("value", [42, "plain", None, True])
def test_redact_sensitive_leaves_scalars_unchanged(value: object) -> None:
    assert redact_sensitive(value) == value


def test_redact_sensitive_prefers_sensitive_over_runtime_regex() -> None:
    # Sensitive match is evaluated before runtime keys in redact_sensitive().
    payload = {"_api_key": "hidden", "_internal": "runtime"}
    assert redact_sensitive(payload) == {
        "_api_key": "[redacted]",
        "_internal": "[runtime object]",
    }


def test_format_json_preview_redacts_and_pretty_prints() -> None:
    text = format_json_preview({"api_key": "secret", "count": 3})
    assert '"api_key": "[redacted]"' in text
    assert '"count": 3' in text


def test_format_json_preview_truncates_long_output() -> None:
    text = format_json_preview({"data": "x" * 5000}, max_chars=100)
    assert text.endswith("\n... [truncated]")
    assert len(text) <= 100


def test_format_json_preview_handles_non_json_serializable_values() -> None:
    text = format_json_preview({1, 2, 3})
    assert isinstance(text, str)
    assert text  # falls back without raising


def test_format_tool_trace_entry_uses_tool_name_precedence() -> None:
    entry = {"tool_name": "PrimaryTool", "key": "FallbackTool", "loop_iteration": 1}
    formatted = format_tool_trace_entry(entry)
    assert "`PrimaryTool`" in formatted
    assert "FallbackTool" not in formatted


def test_format_tool_trace_entry_falls_back_to_key_then_default() -> None:
    assert "`FallbackTool`" in format_tool_trace_entry({"key": "FallbackTool"})
    assert "`tool`" in format_tool_trace_entry({})


@pytest.mark.parametrize(
    "loop_iteration,label",
    [
        (-1, "seed"),
        (0, "iteration 0"),
        (3, "iteration 3"),
    ],
)
def test_format_tool_trace_entry_renders_loop_labels(
    loop_iteration: int,
    label: str,
) -> None:
    formatted = format_tool_trace_entry(
        {"tool_name": "DemoTool", "loop_iteration": loop_iteration},
    )
    assert f"({label})" in formatted


def test_format_tool_trace_entry_pins_absent_loop_iteration() -> None:
    formatted = format_tool_trace_entry({"tool_name": "DemoTool"})
    assert "(iteration None)" in formatted


def test_format_tool_trace_entry_collapses_multiline_previews_to_one_line() -> None:
    entry = {
        "tool_name": "DemoTool",
        "loop_iteration": 1,
        "tool_args": {"filters": {"region": "us-east-1", "status": "open"}},
        "data": {"rows": [1, 2, 3]},
    }
    formatted = format_tool_trace_entry(entry)
    input_preview = formatted.split("input: `", maxsplit=1)[1].split("`", maxsplit=1)[0]
    output_preview = formatted.split("output: `", maxsplit=1)[1].split("`", maxsplit=1)[0]
    assert "\n" not in input_preview
    assert "\n" not in output_preview


def test_format_tool_trace_entry_handles_empty_trace_record() -> None:
    formatted = format_tool_trace_entry({})
    assert "`tool`" in formatted
    assert "input: `{}`" in formatted
    assert "output: `null`" in formatted


def test_format_tool_trace_entry_respects_output_char_limit() -> None:
    entry = {
        "tool_name": "DemoTool",
        "loop_iteration": 0,
        "data": {"payload": "z" * 5000},
    }
    formatted = format_tool_trace_entry(entry, max_output_chars=80)
    output_segment = formatted.split("output: `", maxsplit=1)[1].rstrip("`")
    assert len(output_segment) <= 80
