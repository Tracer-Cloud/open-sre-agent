from __future__ import annotations

import re
from typing import Any

import pytest

from app import output
from app.output import (
    ProgressEvent,
    ProgressTracker,
    _fmt_timing,
    _humanise_message,
    get_output_format,
    get_tracker,
    reset_tracker,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _isolate_output_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a clean slate for output-format env and the tracker singleton."""
    for name in ("TRACER_OUTPUT_FORMAT", "NO_COLOR", "SLACK_WEBHOOK_URL", "TRACER_VERBOSE"):
        monkeypatch.delenv(name, raising=False)
    # The module-level ``_tracker`` is a session-scoped singleton; without resetting it
    # a tracker created in an earlier test would leak its ``_rich`` flag into later ones.
    monkeypatch.setattr(output, "_tracker", None)


@pytest.fixture
def force_text_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the tracker to pick the plain-text rendering path."""
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")


# ─────────────────────────────────────────────────────────────────────────────
# get_output_format
# ─────────────────────────────────────────────────────────────────────────────


def test_get_output_format_honours_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "json")
    assert get_output_format() == "json"


def test_get_output_format_returns_text_when_no_color_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert get_output_format() == "text"


def test_get_output_format_returns_text_when_no_color_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NO_COLOR semantics: presence of the variable is the signal, not its value.
    monkeypatch.setenv("NO_COLOR", "")
    assert get_output_format() == "text"


def test_get_output_format_returns_text_when_slack_webhook_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/abc")
    assert get_output_format() == "text"


def test_get_output_format_returns_rich_for_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(output.sys.stdout, "isatty", lambda: True, raising=False)
    assert get_output_format() == "rich"


def test_get_output_format_returns_text_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(output.sys.stdout, "isatty", lambda: False, raising=False)
    assert get_output_format() == "text"


def test_get_output_format_override_wins_over_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "rich")
    assert get_output_format() == "rich"


# ─────────────────────────────────────────────────────────────────────────────
# _humanise_message
# ─────────────────────────────────────────────────────────────────────────────


def test_humanise_message_returns_empty_for_empty_input() -> None:
    assert _humanise_message("") == ""


def test_humanise_message_uses_registered_tool_display_names() -> None:
    message = "Planned actions: ['query_datadog_logs', 'get_sre_guidance']"

    assert _humanise_message(message) == "Datadog logs, SRE runbook"


def test_humanise_message_falls_back_for_unknown_tool_names() -> None:
    message = "Planned actions: ['my_custom_tool']"

    assert _humanise_message(message) == "my custom tool"


def test_humanise_message_drops_no_new_actions() -> None:
    assert _humanise_message("No new actions to plan") == ""


def test_humanise_message_extracts_resolved_integrations_list() -> None:
    msg = "Resolved integrations: ['datadog', 'grafana', 'pagerduty']"
    assert _humanise_message(msg) == "datadog, grafana, pagerduty"


def test_humanise_message_extracts_integrations_when_keyword_present() -> None:
    msg = "Loaded integrations from store: ['github']"
    assert _humanise_message(msg) == "github"


def test_humanise_message_returns_input_when_resolved_has_no_list() -> None:
    # Falls through to the trailing return when no '[...]' segment is present.
    msg = "resolved without list"
    assert _humanise_message(msg) == msg


def test_humanise_message_formats_validity_as_confidence() -> None:
    assert _humanise_message("validity:87%") == "confidence 87%"


def test_humanise_message_strips_datadog_prefix() -> None:
    assert _humanise_message("datadog:fetched 5 logs") == "fetched 5 logs"


def test_humanise_message_passes_through_unrecognised_messages() -> None:
    assert _humanise_message("ready") == "ready"


# ─────────────────────────────────────────────────────────────────────────────
# _fmt_timing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("elapsed_ms", "expected"),
    [
        (0, "0ms"),
        (1, "1ms"),
        (250, "250ms"),
        (999, "999ms"),
        (1000, "1.0s"),
        (1500, "1.5s"),
        (12345, "12.3s"),
    ],
)
def test_fmt_timing(elapsed_ms: int, expected: str) -> None:
    assert _fmt_timing(elapsed_ms) == expected


# ─────────────────────────────────────────────────────────────────────────────
# ProgressTracker — text mode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_start_prints_node_label_in_text_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")

    out = _strip_ansi(capsys.readouterr().out)
    assert "Gathering evidence" in out
    assert "…" in out


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_start_records_event_and_uses_fallback_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = ProgressTracker()
    tracker.start("custom_node", message="loading")

    out = _strip_ansi(capsys.readouterr().out)
    # Unknown node names are humanised via title-case.
    assert "Custom Node" in out
    assert tracker.events[-1].node_name == "custom_node"
    assert tracker.events[-1].status == "started"
    assert tracker.events[-1].message == "loading"


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_emits_dot_label_and_timing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker = ProgressTracker()

    # ``_finish`` calls ``time.monotonic`` twice: once for the elapsed delta, and
    # once via ``dict.pop(node, time.monotonic())`` whose default is always evaluated
    # before ``pop`` runs — even when ``node`` is present. So we yield three values.
    clock = iter([100.0, 100.5, 100.5])
    monkeypatch.setattr(output.time, "monotonic", lambda: next(clock))

    tracker.start("plan_actions")
    tracker.complete("plan_actions", message="No new actions to plan")

    out = _strip_ansi(capsys.readouterr().out)
    lines = [line for line in out.splitlines() if line.strip()]

    assert lines[-1].strip().startswith("●")
    assert "Planning" in lines[-1]
    assert "500ms" in lines[-1]
    assert "No new actions" not in lines[-1]


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_appends_humanised_message_when_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker = ProgressTracker()

    clock = iter([0.0, 1.25, 1.25])
    monkeypatch.setattr(output.time, "monotonic", lambda: next(clock))

    tracker.start("diagnose_root_cause")
    tracker.complete("diagnose_root_cause", message="validity:75%")

    out = _strip_ansi(capsys.readouterr().out)
    last = [line for line in out.splitlines() if line.strip()][-1]

    assert "Diagnosing" in last
    assert "1.2s" in last
    assert "confidence 75%" in last


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_records_event_with_status_and_fields() -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")
    tracker.complete("investigate", fields_updated=["evidence"], message="datadog:ok")

    completed = [e for e in tracker.events if e.status == "completed"]
    assert len(completed) == 1
    event = completed[0]
    assert event.node_name == "investigate"
    assert event.fields_updated == ["evidence"]
    assert event.message == "datadog:ok"
    assert event.elapsed_ms >= 0


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_error_path_uses_x_marker(capsys: pytest.CaptureFixture[str]) -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")
    tracker.error("investigate", "boom")

    last = [line for line in _strip_ansi(capsys.readouterr().out).splitlines() if line.strip()][-1]
    assert "✗" in last
    assert "Gathering evidence" in last
    assert tracker.events[-1].status == "error"
    assert tracker.events[-1].message == "boom"


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_update_subtext_is_a_noop_in_text_mode() -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")
    # No spinner is registered in text mode, so this must not raise.
    tracker.update_subtext("investigate", "querying logs")
    tracker.complete("investigate")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton tracker
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("force_text_mode")
def test_get_tracker_returns_singleton() -> None:
    a = get_tracker(reset=True)
    b = get_tracker()
    assert a is b


@pytest.mark.usefixtures("force_text_mode")
def test_reset_tracker_creates_a_fresh_instance() -> None:
    first = reset_tracker()
    second = reset_tracker()
    assert first is not second


# ─────────────────────────────────────────────────────────────────────────────
# ProgressEvent dataclass
# ─────────────────────────────────────────────────────────────────────────────


def test_progress_event_defaults() -> None:
    event = ProgressEvent(node_name="investigate", elapsed_ms=10)
    assert event.fields_updated == []
    assert event.status == "completed"
    assert event.message is None


def test_progress_event_independent_default_lists() -> None:
    a: Any = ProgressEvent(node_name="a", elapsed_ms=0)
    b: Any = ProgressEvent(node_name="b", elapsed_ms=0)
    a.fields_updated.append("x")
    assert b.fields_updated == []
