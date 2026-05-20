"""Tests for LLM intent classifier internals and live behavior."""

from __future__ import annotations

import os

import pytest

from app.cli.interactive_shell.routing import llm_intent_classifier as classifier
from app.cli.interactive_shell.routing.types import RouteKind
from app.cli.interactive_shell.runtime.session import ReplSession
from app.config import resolve_llm_settings


def _fresh_session(*, with_prior_state: bool = False) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


@pytest.fixture(autouse=True)
def _clear_lru_cache() -> None:
    classifier.clear_classify_cache()


def _require_live_llm_key() -> None:
    try:
        settings = resolve_llm_settings()
    except Exception as exc:
        pytest.skip(f"Live LLM contract test requires usable LLM configuration: {exc}")
    from app.services.llm_client import reset_llm_singletons

    os.environ["LLM_PROVIDER"] = settings.provider
    reset_llm_singletons()


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
