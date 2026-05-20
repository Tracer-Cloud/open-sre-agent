"""Tests for LLM intent classifier internals and live behavior."""

from __future__ import annotations

import os

import pytest

from app.cli.interactive_shell.orchestration import llm_intent_classifier as classifier
from app.cli.interactive_shell.routing.types import RouteKind
from app.cli.interactive_shell.runtime.session import ReplSession


def _fresh_session(*, with_prior_state: bool = False) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


@pytest.fixture(autouse=True)
def _clear_lru_cache() -> None:
    classifier.clear_classify_cache()


def _require_live_llm_key() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("Live LLM contract test requires ANTHROPIC_API_KEY.")


class TestSanitiseText:
    def test_removes_control_chars(self) -> None:
        cleaned = classifier._sanitise_text("a\x00b\x1fc\x7fd")
        assert cleaned == "abcd"

    def test_neutralizes_prompt_delimiters(self) -> None:
        cleaned = classifier._sanitise_text("keep <<<payload>>> but neutralize <<<< and >>>>")
        assert "<<<" not in cleaned
        assert ">>>" not in cleaned
        assert "payload" in cleaned

    def test_truncates_to_max_length(self) -> None:
        cleaned = classifier._sanitise_text("x" * (classifier._MAX_TEXT_LEN + 17))
        assert len(cleaned) == classifier._MAX_TEXT_LEN

    def test_preserves_braces(self) -> None:
        text = '{"alert": {"service": "orders-api", "status": "firing"}}'
        assert classifier._sanitise_text(text) == text


class TestUserTemplateFormat:
    """Regression tests for Sentry PYTHON-78 (GitHub #2282).

    The old llm_action_planner._call_llm called _SYSTEM_PROMPT.format(...) on a
    prompt containing JSON examples whose keys (e.g. ``\\n  "actions"``) were
    interpreted as missing format-string arguments, raising KeyError at runtime.
    """

    def test_user_template_has_only_text_format_spec(self) -> None:
        import re

        # _USER_TEMPLATE must contain exactly the {text} placeholder and nothing else.
        # Any extra bare format spec (e.g. {actions}) would raise KeyError at runtime
        # when _call_llm passes user-supplied JSON through _USER_TEMPLATE.format(text=...).
        specs = re.findall(r"(?<!\{)\{[^{}]+\}(?!\})", classifier._USER_TEMPLATE)
        assert specs == ["{text}"], (
            f"_USER_TEMPLATE contains unexpected format specs: {specs!r}"
        )

    def test_multiline_json_with_nested_braces_doesnt_raise(self) -> None:
        # Verifies _sanitise_text preserves braces and that the sanitised value
        # embeds correctly in the template (structural, not a KeyError guard).
        payload = '{\n  "actions": [\n    "restart",\n    "rollback"\n  ]\n}'
        sanitised = classifier._sanitise_text(payload)
        result = classifier._USER_TEMPLATE.format(text=sanitised)
        assert sanitised in result

    def test_system_prompt_has_no_bare_format_specs(self) -> None:
        import re

        # Detect single-brace format specs ({key}) that would cause KeyError if
        # _SYSTEM_PROMPT were ever accidentally passed to str.format().
        bare = re.findall(r"(?<!\{)\{[^{}]+\}(?!\})", classifier._SYSTEM_PROMPT)
        assert bare == [], (
            f"_SYSTEM_PROMPT contains format specs that would raise KeyError: {bare!r}"
        )


@pytest.mark.live_llm
class TestLiveClassifierBehavior:
    def test_identical_inputs_remain_stable(self) -> None:
        _require_live_llm_key()
        session = _fresh_session()
        text = "summarize current capabilities in two bullets"
        decision_first = classifier.classify_intent_with_llm(text, session)
        decision_second = classifier.classify_intent_with_llm(text, session)

        if decision_first is not None and decision_second is not None:
            assert decision_first.route_kind == decision_second.route_kind
            return
        assert decision_first is None and decision_second is None

    def test_follow_up_prompt_with_prior_state_never_raises(self) -> None:
        _require_live_llm_key()
        session = _fresh_session(with_prior_state=True)
        decision = classifier.classify_intent_with_llm(
            "what changed since that root cause?", session
        )
        assert decision is None or isinstance(decision.route_kind, RouteKind)

    def test_follow_up_is_overridden_without_prior_state(self) -> None:
        _require_live_llm_key()
        session = _fresh_session(with_prior_state=False)
        decision = classifier.classify_intent_with_llm(
            "what changed since that root cause?", session
        )
        assert decision is None or decision.route_kind != RouteKind.FOLLOW_UP
