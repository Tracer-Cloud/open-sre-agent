"""Tests for the StreamRenderer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

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

        assert renderer._diagnose_buffer == []
        assert renderer._diagnose_live is None
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
        # Updates mode never populates _diagnose_buffer, so the section prints.
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
        # through _finish_diagnose_streaming, which closes the Live region
        # and clears _active_node.
        assert renderer._diagnose_live is None
        assert renderer._active_node is None
