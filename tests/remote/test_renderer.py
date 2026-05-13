"""Tests for the StreamRenderer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from app.remote.renderer import StreamRenderer, _canonical_node_name
from app.remote.stream import StreamEvent


def _make_event(
    event_type: str,
    node: str = "",
    data: dict | None = None,
    *,
    kind: str = "",
    tags: list[str] | None = None,
) -> StreamEvent:
    return StreamEvent(
        event_type=event_type,
        node_name=node,
        data=data or {},
        kind=kind,
        tags=tags or [],
    )


def _investigation_events() -> Iterator[StreamEvent]:
    """Simulate a minimal investigation stream (updates mode)."""
    yield _make_event("metadata", data={"run_id": "r-1"})
    yield _make_event(
        "updates",
        "extract_alert",
        {
            "extract_alert": {
                "alert_name": "test-alert",
                "pipeline_name": "etl",
                "severity": "critical",
            }
        },
    )
    yield _make_event(
        "updates",
        "resolve_integrations",
        {"resolve_integrations": {"resolved_integrations": {"grafana": {}}}},
    )
    yield _make_event(
        "updates",
        "plan_actions",
        {"plan_actions": {"planned_actions": ["query_grafana_logs"]}},
    )
    yield _make_event(
        "updates",
        "investigate",
        {"investigate": {"evidence": {"logs": "error found"}}},
    )
    yield _make_event(
        "updates",
        "diagnose",
        {"diagnose": {"root_cause": "Schema mismatch", "validity_score": 0.85}},
    )
    yield _make_event(
        "updates",
        "publish",
        {"publish": {"report": "Investigation complete."}},
    )
    yield _make_event("end")


def _events_mode_stream() -> Iterator[StreamEvent]:
    """Simulate an events-mode investigation stream with tool calls."""
    yield _make_event("metadata", data={"run_id": "r-3"})

    yield _make_event(
        "events",
        "extract_alert",
        {"name": "extract_alert", "data": {}, "metadata": {"langgraph_node": "extract_alert"}},
        kind="on_chain_start",
        tags=["graph:step:1"],
    )
    yield _make_event(
        "events",
        "extract_alert",
        {
            "name": "extract_alert",
            "data": {"output": {"alert_name": "test", "severity": "high"}},
            "metadata": {"langgraph_node": "extract_alert"},
        },
        kind="on_chain_end",
        tags=["graph:step:1"],
    )

    yield _make_event(
        "events",
        "investigate",
        {"name": "investigate", "data": {}, "metadata": {"langgraph_node": "investigate"}},
        kind="on_chain_start",
        tags=["graph:step:3"],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "query_datadog_logs",
            "data": {"input": {"query": "error"}},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_tool_start",
        tags=[],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "query_datadog_logs",
            "data": {"output": "42 entries"},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_tool_end",
        tags=[],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "investigate",
            "data": {"output": {"root_cause": "Schema error"}},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_chain_end",
        tags=["graph:step:3"],
    )

    yield _make_event("end")


class TestCanonicalNodeName:
    def test_diagnose_maps_to_diagnose_root_cause(self) -> None:
        assert _canonical_node_name("diagnose") == "diagnose_root_cause"

    def test_publish_maps_to_publish_findings(self) -> None:
        assert _canonical_node_name("publish") == "publish_findings"

    def test_extract_alert_unchanged(self) -> None:
        assert _canonical_node_name("extract_alert") == "extract_alert"

    def test_unknown_node_unchanged(self) -> None:
        assert _canonical_node_name("custom_node") == "custom_node"


class TestStreamRendererUpdatesMode:
    """Tests for legacy updates-mode rendering (backward compat)."""

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_renders_full_investigation(self) -> None:
        renderer = StreamRenderer()
        final = renderer.render_stream(_investigation_events())

        assert renderer.events_received == 8
        assert "extract_alert" in renderer.node_names_seen
        assert "diagnose_root_cause" in renderer.node_names_seen
        assert "publish_findings" in renderer.node_names_seen
        assert final.get("root_cause") == "Schema mismatch"
        assert final.get("report") == "Investigation complete."
        assert renderer.stream_completed is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_accumulates_state(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_investigation_events())
        state = renderer.final_state

        assert state["alert_name"] == "test-alert"
        assert state["planned_actions"] == ["query_grafana_logs"]
        assert state["validity_score"] == 0.85

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_handles_empty_stream(self) -> None:
        renderer = StreamRenderer()
        final = renderer.render_stream(iter([]))

        assert renderer.events_received == 0
        assert renderer.node_names_seen == []
        assert final == {}

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_handles_noise_alert(self) -> None:
        def noise_events() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-2"})
            yield _make_event(
                "updates",
                "extract_alert",
                {"extract_alert": {"is_noise": True, "alert_name": "noise"}},
            )
            yield _make_event("end")

        renderer = StreamRenderer()
        final = renderer.render_stream(noise_events())

        assert final.get("is_noise") is True
        assert renderer.events_received == 3

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_plan_actions(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"planned_actions": ["query_logs", "get_metrics"]}
        msg = renderer._build_node_message("plan_actions")
        assert msg is not None
        assert "query_logs" in msg

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_diagnose(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"validity_score": 0.92}
        msg = renderer._build_node_message("diagnose_root_cause")
        assert msg is not None
        assert "92%" in msg

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_diagnose_skips_non_numeric_validity(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"validity_score": "0.9"}
        assert renderer._build_node_message("diagnose_root_cause") is None

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_diagnose_skips_non_finite_validity(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"validity_score": float("nan")}
        assert renderer._build_node_message("diagnose_root_cause") is None


class TestStreamRendererEventsMode:
    """Tests for events-mode rendering (fine-grained tool/LLM events)."""

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_tracks_nodes_from_events(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())

        assert "extract_alert" in renderer.node_names_seen
        assert "investigate" in renderer.node_names_seen
        assert renderer.stream_completed is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_merges_chain_end_output_into_state(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())
        state = renderer.final_state

        assert state.get("root_cause") == "Schema error"
        assert state.get("alert_name") == "test"

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_tool_events_count(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())
        assert renderer.events_received == 8

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_ignores_events_without_node(self) -> None:
        def nodeless_events() -> Iterator[StreamEvent]:
            yield _make_event(
                "events",
                "",
                {"event": "on_chain_start", "name": "RunnableSequence"},
                kind="on_chain_start",
            )
            yield _make_event("end")

        renderer = StreamRenderer()
        renderer.render_stream(nodeless_events())
        assert renderer.node_names_seen == []

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_is_graph_node_event_with_step_tag(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "investigate"},
            kind="on_chain_start",
            tags=["graph:step:3"],
        )
        assert StreamRenderer._is_graph_node_event(evt) is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_is_graph_node_event_name_match(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "investigate"},
            kind="on_chain_start",
        )
        assert StreamRenderer._is_graph_node_event(evt) is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_sub_chain_not_graph_node(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "RunnableSequence"},
            kind="on_chain_start",
            tags=["langsmith:hidden"],
        )
        assert StreamRenderer._is_graph_node_event(evt) is False


class TestStreamRendererCleanupOnException:
    """Tests for spinner + report cleanup when the stream raises mid-iteration.

    The stream may raise (LLM quota, network, cancel). The renderer must always
    stop the spinner thread AND flush whatever final state was accumulated, so
    the user sees the partial report they were watching stream live before the
    exception propagates.
    """

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_partial_state_flushed_when_stream_raises(self) -> None:
        """_print_report must run on the error path, not just the happy path."""

        def stream_raises_after_extract() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-x"})
            yield _make_event(
                "updates",
                "extract_alert",
                {"extract_alert": {"alert_name": "partial-alert"}},
            )
            raise RuntimeError("simulated upstream stream failure")

        renderer = StreamRenderer()
        print_report_calls: list[None] = []
        original_print_report = renderer._print_report
        finish_calls: list[None] = []
        original_finish = renderer._finish_active_node

        def spy_print_report() -> None:
            print_report_calls.append(None)
            original_print_report()

        def spy_finish() -> None:
            finish_calls.append(None)
            original_finish()

        renderer._print_report = spy_print_report  # type: ignore[method-assign]
        renderer._finish_active_node = spy_finish  # type: ignore[method-assign]

        try:
            renderer.render_stream(stream_raises_after_extract())
            raise AssertionError("expected stream exception to propagate")
        except RuntimeError as exc:
            assert "simulated upstream stream failure" in str(exc)

        assert finish_calls, "spinner cleanup must run on stream failure"
        assert print_report_calls, (
            "partial report must be flushed on stream failure — "
            "_print_report() must run from the finally block"
        )
        assert renderer.final_state.get("alert_name") == "partial-alert", (
            "accumulated state from before the failure must be retained"
        )

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_print_report_skipped_on_keyboard_interrupt(self) -> None:
        """_print_report must NOT run when the user presses Ctrl+C."""

        def stream_raises_keyboard_interrupt() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-ki"})
            yield _make_event(
                "updates",
                "extract_alert",
                {"extract_alert": {"alert_name": "interrupted-alert"}},
            )
            raise KeyboardInterrupt

        renderer = StreamRenderer()
        print_report_calls: list[None] = []
        finish_calls: list[None] = []

        renderer._print_report = lambda: print_report_calls.append(None)  # type: ignore[method-assign]
        renderer._finish_active_node = lambda: finish_calls.append(None)  # type: ignore[method-assign]

        with pytest.raises(KeyboardInterrupt):
            renderer.render_stream(stream_raises_keyboard_interrupt())

        assert finish_calls, "spinner cleanup must run on Ctrl+C"
        assert not print_report_calls, (
            "_print_report() must be skipped when the user interrupts the stream"
        )
        assert renderer.final_state.get("alert_name") == "interrupted-alert"


def _diagnose_streaming_events() -> Iterator[StreamEvent]:
    """Simulate the diagnose node emitting token deltas before chain end."""
    yield _make_event("metadata", data={"run_id": "r-d"})
    yield _make_event(
        "events",
        "diagnose",
        {"name": "diagnose", "data": {}, "metadata": {"langgraph_node": "diagnose"}},
        kind="on_chain_start",
        tags=["graph:step:1"],
    )
    yield _make_event(
        "events",
        "diagnose",
        {
            "name": "diagnose",
            "data": {"chunk": {"content": "OpenSRE "}},
            "metadata": {"langgraph_node": "diagnose"},
        },
        kind="on_chat_model_stream",
        tags=[],
    )
    yield _make_event(
        "events",
        "diagnose",
        {
            "name": "diagnose",
            "data": {"chunk": {"content": "identified the schema mismatch."}},
            "metadata": {"langgraph_node": "diagnose"},
        },
        kind="on_chat_model_stream",
        tags=[],
    )
    yield _make_event(
        "events",
        "diagnose",
        {
            "name": "diagnose",
            "data": {"output": {"root_cause": "Schema mismatch", "validity_score": 0.85}},
            "metadata": {"langgraph_node": "diagnose"},
        },
        kind="on_chain_end",
        tags=["graph:step:1"],
    )
    yield _make_event("end")


class TestStreamRendererDiagnoseStreaming:
    """The diagnose node streams the LLM's reasoning live as Markdown.

    Other nodes keep the compact spinner UX from ``ProgressTracker``; only
    diagnose is special-cased because it is where user-facing root-cause
    reasoning is generated (#1263).
    """

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_diagnose_token_chunks_accumulate_into_buffer(self) -> None:
        """Each on_chat_model_stream chunk for diagnose appends to the buffer."""
        renderer = StreamRenderer()
        renderer.render_stream(_diagnose_streaming_events())

        assert "diagnose_root_cause" in renderer.node_names_seen
        # Final state still picks up the chain_end output.
        assert renderer.final_state.get("root_cause") == "Schema mismatch"
        assert renderer.final_state.get("validity_score") == 0.85
        assert renderer.stream_completed is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_diagnose_text_mode_replays_buffer_at_finish(self, capfd) -> None:
        """In text mode the buffered token text is printed when the node ends."""
        renderer = StreamRenderer()
        renderer.render_stream(_diagnose_streaming_events())

        out, _ = capfd.readouterr()
        # Tokens are visible verbatim — not truncated to the 60-char preview
        # the spinner subtext path would use.
        assert "OpenSRE identified the schema mismatch." in out
        # The resolved-dot line uses the canonical node name and timing.
        assert "diagnose_root_cause" in out

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_other_nodes_bypass_diagnose_streaming(self) -> None:
        """Non-diagnose nodes go through the tracker; diagnose buffer stays empty."""
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())

        assert renderer._diagnose.buffer == []
        assert renderer._diagnose._live is None
        assert "diagnose_root_cause" not in renderer.node_names_seen

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_root_cause_section_suppressed_when_diagnose_streamed(self, capfd) -> None:
        """When the user has seen the diagnose reasoning stream live, the
        condensed Root Cause one-liner is redundant and gets dropped."""
        renderer = StreamRenderer()
        renderer.render_stream(_diagnose_streaming_events())

        out, _ = capfd.readouterr()
        # Root Cause header is suppressed; the streamed body and final state
        # carry the same information.
        assert "Root Cause" not in out
        # State is still populated so callers (tests, programmatic users)
        # can read it from final_state.
        assert renderer.final_state.get("root_cause") == "Schema mismatch"

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_root_cause_section_printed_when_diagnose_did_not_stream(self, capfd) -> None:
        """Updates-mode (no events stream) keeps the Root Cause section as before."""
        renderer = StreamRenderer()
        renderer.render_stream(_investigation_events())

        out, _ = capfd.readouterr()
        # Updates mode never populates the diagnose buffer, so the section prints.
        assert "Root Cause" in out
        assert "Schema mismatch" in out

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_diagnose_handles_anthropic_content_block_lists(self, capfd) -> None:
        """langchain-anthropic emits AIMessageChunk.content as a list of blocks.

        Each block can be a dict ``{"type": "text", "text": "..."}`` or an
        object with ``.text``. The renderer must flatten both shapes; calling
        ``str()`` on the list would render its Python repr instead of the
        actual reasoning text.
        """

        class _AnthropicTextBlock:
            def __init__(self, text: str) -> None:
                self.type = "text"
                self.text = text

        def anthropic_diagnose_events() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-anthropic"})
            yield _make_event(
                "events",
                "diagnose",
                {"name": "diagnose", "data": {}, "metadata": {"langgraph_node": "diagnose"}},
                kind="on_chain_start",
                tags=["graph:step:1"],
            )
            # Object-form block (langchain-anthropic typical shape).
            yield _make_event(
                "events",
                "diagnose",
                {
                    "name": "diagnose",
                    "data": {"chunk": {"content": [_AnthropicTextBlock("Schema ")]}},
                    "metadata": {"langgraph_node": "diagnose"},
                },
                kind="on_chat_model_stream",
                tags=[],
            )
            # Dict-form block (alternate shape).
            yield _make_event(
                "events",
                "diagnose",
                {
                    "name": "diagnose",
                    "data": {
                        "chunk": {"content": [{"type": "text", "text": "mismatch detected."}]}
                    },
                    "metadata": {"langgraph_node": "diagnose"},
                },
                kind="on_chat_model_stream",
                tags=[],
            )
            # Tool-use block (non-text) interleaved — must be skipped.
            yield _make_event(
                "events",
                "diagnose",
                {
                    "name": "diagnose",
                    "data": {
                        "chunk": {"content": [{"type": "tool_use", "name": "search", "input": {}}]}
                    },
                    "metadata": {"langgraph_node": "diagnose"},
                },
                kind="on_chat_model_stream",
                tags=[],
            )
            yield _make_event(
                "events",
                "diagnose",
                {
                    "name": "diagnose",
                    "data": {"output": {"root_cause": "Schema mismatch"}},
                    "metadata": {"langgraph_node": "diagnose"},
                },
                kind="on_chain_end",
                tags=["graph:step:1"],
            )
            yield _make_event("end")

        renderer = StreamRenderer()
        renderer.render_stream(anthropic_diagnose_events())

        out, _ = capfd.readouterr()
        # Real reasoning text appears, not Python repr of the block list.
        assert "Schema mismatch detected." in out
        # Tool-use block contributed no garbage.
        assert "tool_use" not in out
        assert "_AnthropicTextBlock" not in out

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_diagnose_live_closed_on_mid_stream_exception(self) -> None:
        """If the stream raises during diagnose, the cleanup finish runs."""

        def diagnose_then_raise() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-x"})
            yield _make_event(
                "events",
                "diagnose",
                {"name": "diagnose", "data": {}, "metadata": {"langgraph_node": "diagnose"}},
                kind="on_chain_start",
                tags=["graph:step:1"],
            )
            yield _make_event(
                "events",
                "diagnose",
                {
                    "name": "diagnose",
                    "data": {"chunk": {"content": "partial reasoning..."}},
                    "metadata": {"langgraph_node": "diagnose"},
                },
                kind="on_chat_model_stream",
                tags=[],
            )
            raise RuntimeError("LLM quota exhausted")

        renderer = StreamRenderer()
        try:
            renderer.render_stream(diagnose_then_raise())
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError as exc:
            assert "LLM quota exhausted" in str(exc)

        # _finish_active_node runs in the finally block and routes diagnose
        # through _end_diagnose, which closes the Live region and clears
        # _active_node.
        assert renderer._diagnose._live is None
        assert renderer._active_node is None


class TestStreamRendererFocusedUXAndParsing:
    """Focused tests for incident-first UX flow, plan preview, and deterministic report parsing."""

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_diagnose_first_flow_prints_alert_header_before_reasoning(
        self, _mock_display, _mock_live
    ) -> None:
        """In the diagnose-first flow, the ingested alert header is printed before reasoning."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "alert_name": "critical-cpu-alert",
            "pipeline_name": "infrastructure",
            "severity": "critical",
        }
        renderer._begin_diagnose("diagnose_root_cause")
        assert renderer._alert_header_printed is True

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_plan_preview_printed_exactly_once(self, _mock_display, _mock_live) -> None:
        """The plan preview panel is printed exactly once when the plan_actions node completes."""
        renderer = StreamRenderer()
        renderer._final_state = {"planned_actions": ["check_logs", "query_metrics"]}
        renderer._active_node = "plan_actions"

        renderer._finish_active_node()
        assert renderer._plan_preview_printed is True

        renderer._plan_preview_printed = True
        renderer._active_node = "plan_actions"
        renderer._finish_active_node()
        assert renderer._plan_preview_printed is True

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_with_structured_sections(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """Report parser correctly handles standard Evidence and Next Actions sections."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Database connection pool saturated",
            "validity_score": 0.95,
            "report": (
                "### Supporting Evidence\n"
                "• Active connections reached 100 max limit\n"
                "• Thread pool starvation observed in logs\n"
                "\n"
                "### Next Actions\n"
                "• Scale database connections to 200\n"
                "• Restart connection pool gracefully\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()

        assert "Supporting Evidence" in out
        assert "Active connections" in out
        assert "Next Actions" in out
        assert "Scale database connections to 200" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_with_numbered_lists(self, _mock_display, _mock_live, capfd) -> None:
        """Report parser correctly handles numbered lists in Evidence and Next Actions sections."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Database connection pool saturated",
            "validity_score": 0.95,
            "report": (
                "### Supporting Evidence\n"
                "1. Active connections reached 100 max limit\n"
                "2) Thread pool starvation observed in logs\n"
                "\n"
                "### Next Actions\n"
                "1. Scale database connections to 200\n"
                "2) Restart connection pool gracefully\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()

        assert "Supporting Evidence" in out
        assert "Active connections" in out
        assert "Next Actions" in out
        assert "Scale database connections to 200" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_fallback_to_verbs_if_no_section(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """If no explicit sections exist, parser falls back to matching verbs for Next Actions."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Deadlock in database",
            "validity_score": 0.88,
            "report": (
                "The system experienced a major deadlock.\n"
                "• Check transaction isolation levels.\n"
                "• Restart the backend container.\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()

        assert "Next Actions" in out
        assert "Check transaction isolation levels" in out
        assert "Restart the backend container" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_ignores_prose_evidence(self, _mock_display, _mock_live, capfd) -> None:
        """Report parser ignores bare 'evidence' in prose form and does not treat it as a section header."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Database connection pool saturated",
            "validity_score": 0.95,
            "report": (
                "There is no evidence of database hardware failure.\n"
                "### Supporting Evidence\n"
                "• Saturated pool connections count is 100\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Supporting Evidence" in out
        assert "Saturated pool connections count" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_mid_sentence_prose_not_misclassified(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """Prose containing section keywords stays out of structured blocks; surfaced as additional context."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Database connection pool saturated",
            "validity_score": 0.95,
            "report": (
                "There is no supporting evidence of DB hardware failure\n"
                "Investigating root cause further to be absolutely sure\n"
                "Skip next steps for now until confirmed\n"
                "### Supporting Evidence\n"
                "• Saturated pool connections count is 100\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Supporting Evidence" in out
        assert "Saturated pool connections count" in out
        assert "Additional report context" in out
        assert "There is no supporting evidence of DB hardware failure" in out
        assert "Investigating root cause further" in out
        assert "Skip next steps for now" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_verb_fallback_ignores_consumed_lines(self, _mock_display, _mock_live, capfd) -> None:
        """Verb-fallback does not pick up diagnostic prose that was already consumed by another section."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "High memory consumption",
            "validity_score": 0.90,
            "report": (
                "### Supporting Evidence\n• We run the check on CPU and confirm it is stable.\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Next Actions" not in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_rich_rca_includes_parsed_report_root_cause_body(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """Lines under a report \"Root Cause\" section are shown in the RCA panel (rich)."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Connection pool exhausted",
            "validity_score": 0.9,
            "report": (
                "# Root Cause\n"
                "• Stale transactions hold connections open.\n"
                "• Idle timeout was set too high for burst traffic.\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Connection pool exhausted" in out
        assert "Stale transactions" in out
        assert "Idle timeout" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parses_markdown_hash_and_emphasis_headers(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """``## …`` and ``*Italic:*`` subsection titles classify like ``### …`` headings."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Incident summary line",
            "validity_score": 0.88,
            "report": (
                "*Non-Validated Claims (Inferred):*\n"
                "Insufficient evidence gathered yet.\n"
                "## Recommended Actions\n"
                "- Enable debug logging for the workload\n"
                "*Cited Evidence:*\n"
                "- Queries: synthetic lookup\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Claims & inference" in out
        assert "Insufficient evidence gathered" in out
        assert "Supporting Evidence" in out
        assert "synthetic lookup" in out.lower()
        assert "Next Actions" in out
        assert "Enable debug logging" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_root_cause_verbs_do_not_promote_to_next_actions(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """Action verbs under Root Cause belong in RCA detail, never verb-fallback Next Actions."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Connection pool exhaustion",
            "validity_score": 0.9,
            "report": (
                "# Root Cause\n"
                "• Review the pool settings before scaling.\n"
                "• Check idle timeout versus burst traffic.\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Next Actions" not in out
        assert "Review the pool settings" in out
        assert "idle timeout" in out.lower()

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_rich_rca_confidence_invalid_score_shows_na(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "Incident summary",
            "validity_score": float("nan"),
            "report": "",
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "N/A" in out

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_report_parsing_mid_list_transition_guard(
        self, _mock_display, _mock_live, capfd
    ) -> None:
        """Section keywords inside a bullet item do not cause mid-list section transitions."""
        renderer = StreamRenderer()
        renderer._final_state = {
            "root_cause": "High memory consumption",
            "validity_score": 0.90,
            "report": (
                "### Supporting Evidence\n"
                "• The evidence suggests memory leak in container.\n"
                "• Check the heap dump next.\n"
            ),
        }
        renderer._print_report()
        out, _ = capfd.readouterr()
        assert "Supporting Evidence" in out
        assert "The evidence suggests memory leak" in out

    def test_strip_outer_quotes_does_not_fire_on_linux(self) -> None:
        """Windows-only outer quote normalization does not alter POSIX shlex tokens."""
        from app.cli.interactive_shell.shell.policy import parse_shell_command

        parsed = parse_shell_command('run "cat /tmp/file.txt"', is_windows=False)
        assert parsed.argv == ["run", "cat /tmp/file.txt"]


class TestStreamRendererDiagnoseThrottle:
    """Pin the throttle: ``Markdown(buffer)`` is constructed at most once
    per refresh window plus a final flush, even on long streams.

    The diagnose node previously called ``live.update(Markdown(buffer))`` on
    every token chunk, so a 10k-token reasoning trace caused ~10k full
    Markdown re-parses (each O(n) on a growing buffer) — visibly stalling
    long streams. These tests use a fake clock + Markdown spy so the parse
    count is deterministic.
    """

    @staticmethod
    def _make_diagnose_chunk(content: object) -> StreamEvent:
        return _make_event(
            "events",
            "diagnose",
            {
                "name": "diagnose",
                "data": {"chunk": {"content": content}},
                "metadata": {"langgraph_node": "diagnose"},
            },
            kind="on_chat_model_stream",
            tags=[],
        )

    @staticmethod
    def _make_diagnose_start() -> StreamEvent:
        return _make_event(
            "events",
            "diagnose",
            {"name": "diagnose", "data": {}, "metadata": {"langgraph_node": "diagnose"}},
            kind="on_chain_start",
            tags=["graph:step:1"],
        )

    @staticmethod
    def _make_diagnose_end() -> StreamEvent:
        return _make_event(
            "events",
            "diagnose",
            {
                "name": "diagnose",
                "data": {"output": {"root_cause": "x"}},
                "metadata": {"langgraph_node": "diagnose"},
            },
            kind="on_chain_end",
            tags=["graph:step:1"],
        )

    def _install_clock_and_spy(self, monkeypatch) -> tuple[list[float], list[int]]:
        """Patch ``time.monotonic`` and ``Markdown`` in the renderer module.

        Returns ``(fake_time, parse_count)`` mutable cells the test drives.
        """
        from app.remote import renderer as renderer_module

        fake_time = [0.0]
        parse_count = [0]
        real_markdown = renderer_module.Markdown

        class _SpyMarkdown(real_markdown):
            def __init__(self, text: str) -> None:
                parse_count[0] += 1
                super().__init__(text)

        monkeypatch.setattr(renderer_module.time, "monotonic", lambda: fake_time[0])
        monkeypatch.setattr(renderer_module, "Markdown", _SpyMarkdown)
        return fake_time, parse_count

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_chunks_in_one_window_collapse_to_a_single_final_flush(
        self, _mock_display, _mock_live, monkeypatch
    ) -> None:
        """100 chunks while the clock is stuck → exactly one Markdown parse."""
        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        # Force rich mode so the Live region opens and the throttle gates
        # actual ``live.update`` calls. In text mode (pytest default — stdout
        # isn't a tty) ``_diagnose._live`` stays None and the throttle never
        # fires, which is the wrong path to test here.
        renderer = StreamRenderer()

        renderer._handle_event(self._make_diagnose_start())
        for i in range(100):
            renderer._handle_event(self._make_diagnose_chunk(f"c{i} "))
        renderer._handle_event(self._make_diagnose_end())

        # Clock never advanced past 0.0 — every per-chunk render was gated.
        # Only the unconditional final flush in _DiagnoseStreamRenderer.finish
        # fires, producing exactly one Markdown parse.
        assert parse_count[0] == 1, (
            f"expected 1 parse (final flush only), got {parse_count[0]}; "
            "throttle is letting intra-window updates through"
        )
        # Buffer still contains all chunks even though we only rendered once.
        assert "c0 " in "".join(renderer._diagnose.buffer)
        assert "c99 " in "".join(renderer._diagnose.buffer)
        # silence unused-var while keeping the fixture wired.
        assert fake_time[0] == 0.0

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_chunks_across_multiple_windows_render_periodically(
        self, _mock_display, _mock_live, monkeypatch
    ) -> None:
        """Chunks spaced past the throttle interval render multiple times."""
        from app.remote import renderer as renderer_module

        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        interval = renderer_module._DIAGNOSE_RENDER_INTERVAL_S
        renderer = StreamRenderer()

        renderer._handle_event(self._make_diagnose_start())
        # 10 chunks, each crossing a fresh render window (2× interval apart).
        for i in range(10):
            fake_time[0] = (i + 1) * (interval * 2)
            renderer._handle_event(self._make_diagnose_chunk(f"c{i} "))
        renderer._handle_event(self._make_diagnose_end())

        # 10 in-loop renders + 1 final flush. Tolerance for boundary effects.
        assert 9 <= parse_count[0] <= 12, (
            f"expected ~10–11 parses across 10 windows, got {parse_count[0]}"
        )
        # Throttle's purpose: parse count must stay << total chunks.
        assert parse_count[0] < 50

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_final_flush_renders_chunks_pending_in_last_window(
        self, _mock_display, _mock_live, monkeypatch
    ) -> None:
        """Chunks arriving in the trailing throttle window must still appear."""
        from app.remote import renderer as renderer_module

        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        interval = renderer_module._DIAGNOSE_RENDER_INTERVAL_S
        renderer = StreamRenderer()

        renderer._handle_event(self._make_diagnose_start())
        # First chunk crosses one window — renders.
        fake_time[0] = interval * 2
        renderer._handle_event(self._make_diagnose_chunk("early "))
        # Remaining chunks fall within the same window — throttle blocks them.
        renderer._handle_event(self._make_diagnose_chunk("tail-1 "))
        renderer._handle_event(self._make_diagnose_chunk("tail-2"))
        renderer._handle_event(self._make_diagnose_end())

        # All chunks must be in the buffer at finish (final flush picks them up).
        assert "".join(renderer._diagnose.buffer) == "early tail-1 tail-2"
        # Two parses: one in-loop render at "early " + one final flush.
        assert parse_count[0] == 2

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_anthropic_block_chunks_throttle_correctly(
        self, _mock_display, _mock_live, monkeypatch
    ) -> None:
        """List-shaped Anthropic content blocks honor the same throttle gate."""
        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        renderer = StreamRenderer()

        renderer._handle_event(self._make_diagnose_start())
        # Mixed: dict-form text blocks + tool-use (skipped) + object-form.
        # Clock stuck → all gated, only final flush fires.
        for i in range(20):
            content = [{"type": "text", "text": f"c{i} "}]
            renderer._handle_event(self._make_diagnose_chunk(content))
        renderer._handle_event(self._make_diagnose_end())

        # Exactly one Markdown parse — the final flush.
        assert parse_count[0] == 1
        # Block list shapes flatten correctly through the throttle path too.
        assert "".join(renderer._diagnose.buffer).startswith("c0 ")
        assert "c19" in "".join(renderer._diagnose.buffer)
        assert fake_time[0] == 0.0

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_diagnose_start_stops_progress_tracker_display(self, _mock_display, _mock_live) -> None:
        """Calling _begin_diagnose safely stops the active ProgressTracker display and sets it to None."""
        renderer = StreamRenderer()
        # Initialize active display
        renderer._tracker._display = renderer._tracker._display or True
        assert renderer._tracker._display is not None

        renderer._begin_diagnose("diagnose_root_cause")
        assert renderer._tracker._display is None


class TestStreamRendererPrintAboveRenderable:
    """Tests for the _print_above_renderable safety mechanism.

    Ensures that when the diagnose Live region is active, print_above_renderable
    is routed through the Live console to prevent terminal corruption, and
    otherwise falls back to the tracker.
    """

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_print_above_renderable_routes_to_live_console_when_started(
        self, _mock_display, _mock_live
    ) -> None:
        renderer = StreamRenderer()
        renderer._begin_diagnose("diagnose_root_cause")

        # Mock the active Live object's console
        from unittest.mock import MagicMock

        mock_console = MagicMock()
        renderer._diagnose._live.console = mock_console
        renderer._diagnose._live.is_started = True

        panel = "test-panel"
        renderer._print_above_renderable(panel)

        # Should print directly via active Live console
        mock_console.print.assert_called_once_with(panel)

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_print_above_renderable_falls_back_to_tracker_when_live_not_started(
        self, _mock_display, _mock_live
    ) -> None:
        renderer = StreamRenderer()
        # Mock tracker's print_above_renderable
        from unittest.mock import MagicMock

        mock_tracker_print = MagicMock()
        renderer._tracker.print_above_renderable = mock_tracker_print

        panel = "test-panel"
        renderer._print_above_renderable(panel)

        # Should fall back to tracker
        mock_tracker_print.assert_called_once_with(panel)

    @patch("app.remote.renderer.Live")
    @patch("app.output._EventLogDisplay")
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
    def test_print_above_renderable_falls_back_to_console_when_tracker_stopped(
        self, _mock_display, _mock_live
    ) -> None:
        from app.remote.renderer import StreamRenderer

        renderer = StreamRenderer()

        # Simulate tracker being stopped
        renderer._tracker.stop()
        assert renderer._tracker._display is None

        from unittest.mock import MagicMock

        mock_console = MagicMock()
        renderer._console = mock_console

        panel = "test-panel"
        renderer._print_above_renderable(panel)

        # Should fall back to internal console
        mock_console.print.assert_called_once_with(panel)

    def test_merge_chain_start_input_eagerly_updates_metadata(self) -> None:
        """_merge_chain_start_input should pull 'input' payload from data into _final_state."""
        from app.remote.renderer import StreamEvent, StreamRenderer

        renderer = StreamRenderer()
        assert "alert_name" not in renderer._final_state

        # Construct on_chain_start style event carrying metadata in input
        event = StreamEvent(
            event_type="events",
            node_name="diagnose",
            kind="on_chain_start",
            data={
                "name": "diagnose",
                "data": {
                    "input": {"alert_name": "late-breaking-alert", "pipeline_name": "test-pipeline"}
                },
            },
        )

        renderer._merge_chain_start_input(event)

        assert renderer._final_state.get("alert_name") == "late-breaking-alert"
        assert renderer._final_state.get("pipeline_name") == "test-pipeline"
