"""Unit tests for non-command routing LLM-only fallback behavior.

Guardrail:
- These tests enforce policy that deterministic non-command fallback must
  never be reintroduced (regex/keyword/rule routing is disallowed here).
"""

from __future__ import annotations

from app.cli.interactive_shell.routing.message_route import handle_message_with_agent
from app.cli.interactive_shell.routing.types import RouteDecision, RouteKind
from app.cli.interactive_shell.runtime.session import ReplSession


def test_handle_message_with_agent_returns_llm_decision_when_available() -> None:
    expected = RouteDecision(RouteKind.CLI_HELP, 0.93, ("llm_intent_classifier",))

    decision = handle_message_with_agent(
        "how do I configure datadog?",
        ReplSession(),
        llm_resolver=lambda _text, _session: expected,
    )

    assert decision == expected


def test_fallback_does_not_regex_route_help_text_when_llm_returns_none() -> None:
    # No regex fallback allowed: help-like text still falls back to cli_agent.
    decision = handle_message_with_agent(
        "how do I run an investigation?",
        ReplSession(),
        llm_resolver=lambda _text, _session: None,
    )

    assert decision.route_kind == RouteKind.CLI_AGENT
    assert decision.matched_signals == ()
    assert decision.fallback_reason == "no_match"


def test_fallback_does_not_regex_route_json_alert_when_llm_returns_none() -> None:
    # No regex fallback allowed: JSON alert-like text still falls back to cli_agent.
    decision = handle_message_with_agent(
        '{"alertname":"HighCPU","severity":"critical"}',
        ReplSession(),
        llm_resolver=lambda _text, _session: None,
    )

    assert decision.route_kind == RouteKind.CLI_AGENT
    assert decision.matched_signals == ()
    assert decision.fallback_reason == "no_match"


def test_fallback_does_not_regex_route_follow_up_with_prior_state() -> None:
    # No regex fallback allowed: short follow-up text must not auto-route.
    session = ReplSession()
    session.last_state = {"summary": "orders-api latency spike"}

    decision = handle_message_with_agent(
        "why did it fail?",
        session,
        llm_resolver=lambda _text, _session: None,
    )

    assert decision.route_kind == RouteKind.CLI_AGENT
    assert decision.matched_signals == ()
    assert decision.fallback_reason == "no_match"


def test_fallback_keeps_cli_agent_and_error_reason_when_llm_raises() -> None:
    # No regex fallback allowed after classifier failure.
    def _raise(_text: str, _session: ReplSession) -> RouteDecision | None:
        raise RuntimeError("classifier unavailable")

    decision = handle_message_with_agent(
        "The deploy of orders-api is failing with 500 errors in canary.",
        ReplSession(),
        llm_resolver=_raise,
    )

    assert decision.route_kind == RouteKind.CLI_AGENT
    assert decision.matched_signals == ()
    assert decision.fallback_reason == "llm_error_no_match"
