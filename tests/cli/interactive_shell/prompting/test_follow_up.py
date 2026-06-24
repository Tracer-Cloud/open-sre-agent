"""Tests for follow-up summarization and evidence handling."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

from rich.console import Console

from app.cli.interactive_shell.prompting.follow_up import (
    _record_follow_up_turn,
    _summarize_evidence,
    _summarize_last_state,
    answer_follow_up,
)
from app.cli.interactive_shell.runtime.session import ReplSession


class _StreamingClient:
    """Streaming-aware fake: ``invoke_stream`` yields the canned content as one chunk."""

    def __init__(self, content: str) -> None:
        self._content = content

    def invoke_stream(self, _prompt: str) -> Iterator[str]:
        yield self._content


class TestSummarizeEvidence:
    def test_dict_evidence_includes_keys_and_sample(self) -> None:
        evidence = {
            "ev-1": {"kind": "log", "text": "disk full"},
            "ev-2": {"kind": "metric", "value": 99.8},
            "ev-3": {"kind": "trace", "latency_ms": 4200},
            "ev-4": {"kind": "event", "message": "restarted pod"},
        }
        parts = _summarize_evidence(evidence)
        joined = "\n".join(parts)
        assert "Evidence items: 4" in joined
        assert "ev-1" in joined
        assert "ev-2" in joined
        assert "ev-3" in joined
        # Only the first 3 keys are sampled into the JSON body.
        assert "ev-4" not in joined

    def test_list_evidence_slices_first_three(self) -> None:
        evidence = [{"id": i, "kind": "log", "text": f"line {i}"} for i in range(10)]
        parts = _summarize_evidence(evidence)
        joined = "\n".join(parts)
        assert "Evidence items: 10" in joined
        assert '"id": 0' in joined
        assert '"id": 2' in joined
        assert '"id": 3' not in joined

    def test_other_type_falls_back_to_string(self) -> None:
        parts = _summarize_evidence("a raw evidence blob")
        joined = "\n".join(parts)
        assert "Evidence type: str" in joined
        assert "a raw evidence blob" in joined


class TestSummarizeLastState:
    def test_dict_evidence_is_grounded_not_dropped(self) -> None:
        # Regression: evidence used to be indexed as a list (evidence[:3]),
        # which raised TypeError for the real dict shape and silently dropped
        # all evidence context from follow-up prompts.
        state = {
            "alert_name": "Orders API 5xx spike",
            "root_cause": "Redis cache eviction storm",
            "evidence": {
                "ev-1": {"kind": "log", "text": "OOMKilled"},
                "ev-2": {"kind": "metric", "name": "cache_hit_rate", "value": 0.12},
            },
        }
        summary = _summarize_last_state(state)
        assert "Evidence items: 2" in summary
        assert "ev-1" in summary
        assert "OOMKilled" in summary
        assert "(evidence present but could not be serialized" not in summary

    def test_empty_state_uses_placeholder(self) -> None:
        assert _summarize_last_state({}) == "(no prior investigation details available)"

    def test_missing_evidence_is_silent(self) -> None:
        state = {"alert_name": "Test", "root_cause": "unknown"}
        summary = _summarize_last_state(state)
        assert "Alert: Test" in summary
        assert "Evidence" not in summary


class TestAnswerFollowUpMarkupSafety:
    """Regression: LLM output with bracket sequences (e.g. [OOMKilled]) was
    being silently truncated by Rich's markup parser. The streaming renderer
    routes text through Markdown so brackets in plain prose survive."""

    def _run_with_response(self, monkeypatch: object, response_text: str) -> str:
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            lambda: _StreamingClient(response_text),
        )

        session = ReplSession()
        session.last_state = {"alert_name": "test", "root_cause": "x"}

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, highlight=False, width=200)
        answer_follow_up("what happened?", session, console)
        return buf.getvalue()

    def test_llm_output_with_brackets_not_truncated(self, monkeypatch: object) -> None:
        response = (
            "The pod hit [OOMKilled] at [2026-04-15 14:00:00 UTC] on "
            "[service-name=orders-api] - see [ERROR] in logs."
        )
        output = self._run_with_response(monkeypatch, response)
        # Markdown rendering may break a long line; strip newlines so the
        # bracketed tokens are still discoverable as substrings.
        flat = re.sub(r"\s+", " ", output)
        assert "[OOMKilled]" in flat
        assert "[ERROR]" in flat
        assert "orders-api" in flat
        assert "2026-04-15 14:00:00 UTC" in flat

    def test_exception_message_with_brackets_not_dropped(self, monkeypatch: object) -> None:
        captured_errors: list[BaseException] = []

        def _boom() -> None:
            raise RuntimeError("config error: missing [api_key] in [datadog] section")

        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            _boom,
        )
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.cli.interactive_shell.error_handling.exception_reporting.capture_exception",
            lambda exc, **_kwargs: captured_errors.append(exc),
        )

        session = ReplSession()
        session.last_state = {"alert_name": "test", "root_cause": "x"}

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, highlight=False, width=200)
        answer_follow_up("why?", session, console)
        output = buf.getvalue()
        assert "[api_key]" in output
        assert "[datadog]" in output
        assert len(captured_errors) == 1
        assert isinstance(captured_errors[0], RuntimeError)


class TestAnswerFollowUpGroundingContract:
    """Verifies that the final-state identifiers (evidence, root cause) are explicitly
    propagated into the generated LLM prompt, satisfying acceptance criteria for
    'grounded follow-ups'."""

    def test_prompt_grounds_question_with_root_cause_and_evidence(
        self, monkeypatch: object
    ) -> None:
        captured_prompts: list[str] = []

        class _SpyClient:
            def invoke_stream(self, prompt: str) -> Iterator[str]:
                captured_prompts.append(prompt)
                yield "Success"

        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            lambda: _SpyClient(),
        )

        session = ReplSession()
        session.last_state = {
            "alert_name": "Target_Alert_X",
            "root_cause": "Database lock contention identified",
            "evidence": {
                "ev-999": {"kind": "trace", "summary": "Query execution exceeded 10000ms"}
            },
        }

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        answer_follow_up("Why is there a lock?", session, console)

        assert len(captured_prompts) == 1
        final_prompt = captured_prompts[0]

        # Verify strict grounding elements in input prompt
        assert "Target_Alert_X" in final_prompt
        assert "Database lock contention identified" in final_prompt
        assert "ev-999" in final_prompt
        assert "Query execution exceeded 10000ms" in final_prompt
        assert "Why is there a lock?" in final_prompt


class TestFollowUpMultiTurn:
    """Verifies that follow-up answers are stored and included in subsequent prompts."""

    def _make_spy_client(self, captured_prompts: list[str], response: str = "Answer") -> object:
        class _SpyClient:
            def invoke_stream(self, prompt: str) -> Iterator[str]:
                captured_prompts.append(prompt)
                yield response

        return _SpyClient()

    def _session_with_state(self) -> ReplSession:
        session = ReplSession()
        session.last_state = {"alert_name": "Spike", "root_cause": "cache miss"}
        return session

    def test_first_follow_up_records_to_follow_up_messages(self, monkeypatch: object) -> None:
        captured: list[str] = []
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            lambda: self._make_spy_client(captured, "It was a cache miss."),
        )
        session = self._session_with_state()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)

        answer_follow_up("why did it fail?", session, console)

        assert len(session.follow_up_messages) == 2
        assert session.follow_up_messages[0] == ("user", "why did it fail?")
        assert session.follow_up_messages[1] == ("assistant", "It was a cache miss.")
        # cli_agent_messages must remain untouched
        assert len(session.cli_agent_messages) == 0

    def test_second_follow_up_sees_first_answer_in_prompt(self, monkeypatch: object) -> None:
        captured: list[str] = []
        call_count = 0

        class _SequentialClient:
            def invoke_stream(self, prompt: str) -> Iterator[str]:
                nonlocal call_count
                call_count += 1
                captured.append(prompt)
                yield f"Answer {call_count}"

        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            _SequentialClient,
        )
        session = self._session_with_state()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)

        answer_follow_up("why did it fail?", session, console)
        answer_follow_up("how do we fix it?", session, console)

        assert call_count == 2
        second_prompt = captured[1]
        # First Q&A pair must appear in the second prompt
        assert "why did it fail?" in second_prompt
        assert "Answer 1" in second_prompt
        assert "how do we fix it?" in second_prompt
        assert "Prior follow-up conversation" in second_prompt

    def test_empty_response_not_recorded(self, monkeypatch: object) -> None:
        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            lambda: self._make_spy_client([], ""),
        )
        session = self._session_with_state()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)

        answer_follow_up("why?", session, console)

        assert len(session.follow_up_messages) == 0

    def test_record_follow_up_turn_caps_at_max(self) -> None:
        session = ReplSession()
        for i in range(14):
            _record_follow_up_turn(session, f"q{i}", f"a{i}")
        # Cap is 12 pairs = 24 entries; 14 pairs = 28 entries → trimmed to 24
        assert len(session.follow_up_messages) == 24
        # Oldest two pairs (q0, q1) are gone; newest survive
        roles_and_contents = session.follow_up_messages
        assert roles_and_contents[0] == ("user", "q2")
        assert roles_and_contents[-1] == ("assistant", "a13")

    def test_cli_agent_turns_do_not_bleed_into_follow_up_history(self, monkeypatch: object) -> None:
        """Regression: follow_up_messages must be isolated from cli_agent_messages.

        A cli_agent exchange before/after a follow-up must never appear in the
        follow-up grounding prompt, as it is unrelated to the investigation.
        """
        captured: list[str] = []

        class _SpyClient:
            def invoke_stream(self, prompt: str) -> Iterator[str]:
                captured.append(prompt)
                yield "Follow-up answer"

        monkeypatch.setattr(  # type: ignore[attr-defined]
            "app.services.llm_client.get_llm_for_reasoning",
            _SpyClient,
        )
        session = self._session_with_state()
        # Simulate prior cli_agent turns (e.g. "tell me about opensre")
        session.cli_agent_messages.append(("user", "tell me about opensre"))
        session.cli_agent_messages.append(("assistant", "OpenSRE is an SRE tool."))

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        answer_follow_up("why did it fail?", session, console)

        assert captured, "LLM was not called"
        prompt = captured[0]
        # The cli_agent exchange must NOT appear in the follow-up prompt
        assert "tell me about opensre" not in prompt
        assert "OpenSRE is an SRE tool" not in prompt
        # The follow-up question itself must be present
        assert "why did it fail?" in prompt
