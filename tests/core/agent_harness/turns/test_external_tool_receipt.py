"""A summary-less tool payload reaches the console in full and chat as a receipt.

The failure this pins: ``kubernetes_get_pod_logs`` returns no ``summary`` key,
so its whole payload was dumped into ``response_text`` and posted to Slack —
14.2 MB of customer log lines in one thread. The console keeps the dump; the
external surface must not.
"""

from __future__ import annotations

import json
from typing import Any

from core.agent_harness.turns.action_driver import (
    _compose_response,
    _format_generic_tool_payload,
    _show_response,
    _TurnCounts,
)
from core.agent_harness.turns.tool_receipt import (
    NO_ANSWER_FOOTER,
    RECEIPT_MAX_ARG_VALUE_CHARS,
    format_char_size,
    format_tool_dump_receipt,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

#: Stands in for a customer email or any other content the tool merely relayed.
_MARKER = "leaked-payload-marker"

_POD_LOG_ARGS = {"pod_name": "api-worker-7d9f4b", "namespace": "payments"}


class _Call:
    def __init__(self, name: str, tool_input: dict[str, Any]) -> None:
        self.name = name
        self.input = tool_input


class _ToolResult:
    """A tool result with no summary-ish key — the raw-dump fallback path."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.content = json.dumps(payload)
        self.details = None
        self.is_error = False


class _Result:
    def __init__(self, tool_results: list[tuple[_Call, Any]], final_text: str) -> None:
        self.tool_results = tool_results
        self.executed = tool_results
        self.planned = [call for call, _ in tool_results]
        self.final_text = final_text


class _Session:
    history: list[dict[str, Any]] = []


def _counts() -> _TurnCounts:
    return _TurnCounts(
        executed_entries=[],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=1,
        planned_count=1,
        handled=True,
        investigation_dispatched=False,
        handoff_contents=(),
    )


def _pod_log_turn(final_text: str) -> _Result:
    call = _Call("kubernetes_get_pod_logs", dict(_POD_LOG_ARGS))
    payload = {"lines": [f"2026-08-08 ERROR {_MARKER} {i}" for i in range(40)], "total": 40}
    return _Result([(call, _ToolResult(payload))], final_text)


def test_a_summaryless_payload_is_dumped_locally_and_receipted_externally() -> None:
    # Arrange: the exact shape kubernetes_get_pod_logs returns — no summary key.
    call = _Call("kubernetes_get_pod_logs", dict(_POD_LOG_ARGS))
    payload = {"lines": [f"line {i} {_MARKER}" for i in range(40)], "total": 40}

    # Act
    rendered = _format_generic_tool_payload(call, _ToolResult(payload))

    # Assert: the console is not an external surface and keeps the evidence.
    assert _MARKER in rendered.local
    # The chat surface gets enough to know what ran and how big it was, and
    # none of what the tool read.
    assert _MARKER not in rendered.external
    assert "kubernetes_get_pod_logs" in rendered.external
    assert "pod_name=api-worker-7d9f4b" in rendered.external
    assert "40 records" in rendered.external


def test_a_tool_that_summarises_itself_renders_identically_on_both_surfaces() -> None:
    # Arrange: the receipt must not fire for tools that already speak prose,
    # or every well-behaved tool loses its answer on chat.
    call = _Call("some_registry_tool", {})
    result = _ToolResult({"summary": f"Restarted 3 pods. {_MARKER}"})

    # Act
    rendered = _format_generic_tool_payload(call, result)

    # Assert
    assert rendered.local == rendered.external
    assert _MARKER in rendered.external


def test_the_turn_carries_a_separate_external_text_when_a_payload_was_reduced() -> None:
    # Arrange: no closing prose from the model, so response_text *is* the dump.
    # This is the answered=False case from the incident.
    composed = _compose_response(_pod_log_turn(""), _Session(), _counts())

    # Assert: response_text keeps the payload for the console and persistence…
    assert _MARKER in composed.response_text
    # …and the chat variant exists, is free of it, and says why it is terse.
    assert composed.external_response_text is not None
    assert _MARKER not in composed.external_response_text
    assert NO_ANSWER_FOOTER in composed.external_response_text


def test_a_real_closing_report_is_sent_to_chat_unchanged() -> None:
    # Arrange: the model wrote a genuine multi-line answer, so response_text is
    # its prose — there is no payload in it to replace. Redacting anyway would
    # throw away the answer on every turn that also called a summary-less tool.
    report = "## Root cause\n\nThe payments worker OOMed twice in ten minutes."
    composed = _compose_response(_pod_log_turn(report), _Session(), _counts())

    # Assert
    assert composed.response_text == report
    assert composed.external_response_text is None


def test_a_receipt_never_republishes_the_argument_it_is_describing() -> None:
    """Pins an argument value that is itself the payload — a filter, a query, a manifest."""
    receipt = format_tool_dump_receipt("some_tool", {"query": _MARKER * 20}, payload_chars=5_000)

    assert _MARKER * 20 not in receipt
    assert len(receipt) < 200


def test_size_reads_in_the_unit_a_human_would_pick() -> None:
    """Pins ``1000.0 KB`` — rounding must choose the unit, not the raw division."""
    assert format_char_size(812) == "812 chars"
    assert format_char_size(60_000) == "60.0 KB"
    assert format_char_size(999_999) == "1.0 MB"
    assert format_char_size(1_149_165) == "1.1 MB"


def test_a_receipt_for_an_argument_less_call_is_still_a_sentence() -> None:
    """Pins ``some_tool () returned …`` — several tools take no arguments at all."""
    assert format_tool_dump_receipt("some_tool", {}, payload_chars=5_000).startswith(
        "some_tool returned"
    )


def test_receipt_arguments_stay_short_enough_to_read() -> None:
    long_value = "z" * (RECEIPT_MAX_ARG_VALUE_CHARS * 3)
    receipt = format_tool_dump_receipt("some_tool", {"name": long_value}, payload_chars=10)

    assert long_value not in receipt
    assert "name=zzz" in receipt


class _RecordingSink:
    """The console: not a shared surface, so it keeps the raw evidence."""

    def __init__(self) -> None:
        self.printed: list[str] = []

    def print(self, message: str = "") -> None:
        self.printed.append(message)

    def render_response_header(self, label: str) -> None:
        self.printed.append(f"[{label}]")

    @property
    def text(self) -> str:
        return "\n".join(self.printed)


class _RedactingSink(_RecordingSink):
    """A chat surface: shared with people who did not ask for the payload."""

    redacts_raw_tool_output = True


def _render(sink: _RecordingSink) -> str:
    """Compose a real summary-less turn and show it through ``sink``."""
    composed = _compose_response(_pod_log_turn(""), _Session(), _counts())
    _show_response(
        sink,  # type: ignore[arg-type]
        handled=True,
        final_text=composed.response_text if composed.use_final_text else "",
        display_chunks=composed.display_chunks,
        external_display_chunks=composed.external_display_chunks,
    )
    return sink.text


def test_a_chat_surface_is_shown_the_receipt_and_never_the_payload() -> None:
    """The live leak: this is the path that posted customer log lines to Slack.

    ``_show_response`` runs *inside* the turn, before ``finalize``, so bounding
    the final answer alone left the payload flowing out through here — as an
    unbounded Slack timeline row title.
    """
    rendered = _render(_RedactingSink())

    assert _MARKER not in rendered
    assert "kubernetes_get_pod_logs" in rendered


def test_the_console_still_shows_the_whole_payload() -> None:
    """Redacting locally would cost the operator the evidence they are reading."""
    rendered = _render(_RecordingSink())

    assert _MARKER in rendered


def _turn_result(*, assistant_text: str, external_text: str | None, action_text: str) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text=action_text,
            external_response_text=external_text,
        ),
        assistant_response_text=assistant_text,
    )


def test_a_turn_reduced_to_a_receipt_did_not_produce_an_answer() -> None:
    """The signal behind the ✓/✗ on the last timeline row.

    A receipt exists exactly when the assistant wrote nothing, so a turn whose
    outbound text *is* the receipt handed the reader a note about a tool call
    instead of the answer they asked for. ``answered`` cannot see this — it only
    reports whether the text was streamed.
    """
    turn = _turn_result(
        assistant_text="", external_text="ran kubernetes_get_pod_logs", action_text="raw dump"
    )

    assert turn.external_primary_response_text == "ran kubernetes_get_pod_logs"
    assert turn.produced_an_answer is False


def test_an_unstreamed_action_answer_is_still_an_answer() -> None:
    """The common case, and the one a naive ``not answered`` check gets wrong."""
    turn = _turn_result(assistant_text="", external_text=None, action_text="Restarted 3 pods.")

    assert turn.answered is False
    assert turn.produced_an_answer is True


def test_assistant_prose_is_an_answer_even_when_a_payload_was_receipted() -> None:
    """Model prose is safe to send as-is, so a receipt beside it is not a failure."""
    turn = _turn_result(
        assistant_text="Three pods are crash-looping on an OOM.",
        external_text="ran kubernetes_get_pod_logs",
        action_text="raw dump",
    )

    assert turn.produced_an_answer is True
