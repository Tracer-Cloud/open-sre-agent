"""Extensive tests for LLM-based intent routing.

Test strategy:
  - Unit tests for ``classify_intent_with_llm`` with a mocked LLM to verify
    parse/dispatch logic without incurring real API costs.
  - Integration-style tests for ``route_input`` that patch the LLM classifier
    to simulate various LLM responses, confirming the router honours LLM
    decisions and falls back gracefully when the LLM is unavailable.
  - End-to-end routing correctness tests with the LLM disabled (env flag)
    that re-assert the regex fallback still handles every canonical case.
  - Edge-case parametrized tests covering the specific scenarios that broke
    under pure regex routing (e.g. alert vocabulary in synthetic test IDs).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.cli.interactive_shell.llm_intent_classifier import (
    classify_intent_with_llm,
    clear_classify_cache,
)
from app.cli.interactive_shell.router import RouteDecision, RouteKind, route_input
from app.cli.interactive_shell.session import ReplSession

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _fresh_session(*, with_prior_state: bool = False) -> ReplSession:
    session = ReplSession()
    if with_prior_state:
        session.last_state = {"root_cause": "disk full on orders-api"}
    return session


@pytest.fixture(autouse=True)
def _clear_lru_cache() -> None:  # type: ignore[return]
    """Evict the LRU cache before every test so earlier calls don't bleed through."""
    clear_classify_cache()


@pytest.fixture()
def _disable_llm_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable LLM routing so tests exercise only the regex fallback path."""
    monkeypatch.setenv("OPENSRE_DISABLE_LLM_ROUTING", "1")
    # The module-level constant is read once at import time, so we patch the
    # flag on the module object directly.
    monkeypatch.setattr("app.cli.interactive_shell.router._LLM_ROUTING_DISABLED", True)


def _mock_llm_response(response_text: str) -> MagicMock:
    """Return a mock LLM client whose ``invoke`` returns *response_text*."""
    mock_response = MagicMock()
    mock_response.content = response_text
    mock_client = MagicMock()
    mock_client.invoke.return_value = mock_response
    return mock_client


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unit tests: classify_intent_with_llm()
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyIntentWithLLM:
    """Tests for the raw LLM classifier function."""

    def test_llm_cli_agent_response_parsed(self) -> None:
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("cli_agent"),
        ):
            decision = classify_intent_with_llm(
                "run synthetic test 002-connection-exhaustion", session
            )
        assert decision is not None
        assert decision.route_kind == RouteKind.CLI_AGENT
        assert decision.matched_signals == ("llm_intent_classifier",)

    def test_llm_new_alert_response_parsed(self) -> None:
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("new_alert"),
        ):
            decision = classify_intent_with_llm(
                "CPU is spiking to 99% on the orders-api pods", session
            )
        assert decision is not None
        assert decision.route_kind == RouteKind.NEW_ALERT

    def test_llm_follow_up_response_parsed(self) -> None:
        session = _fresh_session(with_prior_state=True)
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("follow_up"),
        ):
            decision = classify_intent_with_llm("why did it fail?", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.FOLLOW_UP

    def test_llm_cli_help_response_parsed(self) -> None:
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("cli_help"),
        ):
            decision = classify_intent_with_llm("how do I configure datadog?", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.CLI_HELP

    def test_llm_slash_response_parsed(self) -> None:
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("slash"),
        ):
            decision = classify_intent_with_llm("/status", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.SLASH

    def test_llm_response_with_extra_whitespace_parsed(self) -> None:
        """LLM may include leading/trailing whitespace; parse should still work."""
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("  cli_agent  "),
        ):
            decision = classify_intent_with_llm("show connected services", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_llm_response_wrapped_in_sentence_extracted(self) -> None:
        """Even if LLM adds explanation, the route word should be extracted."""
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response(
                "Based on the input this should be classified as: new_alert"
            ),
        ):
            decision = classify_intent_with_llm("502s on checkout service", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.NEW_ALERT

    def test_llm_garbage_response_returns_none(self) -> None:
        """An unparseable LLM response should return None (trigger fallback)."""
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("I cannot classify this."),
        ):
            decision = classify_intent_with_llm("some input", session)
        assert decision is None

    def test_llm_unavailable_returns_none(self) -> None:
        """ImportError from the LLM client must return None, not raise."""
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            side_effect=ImportError("no llm client"),
        ):
            decision = classify_intent_with_llm("database is slow", session)
        assert decision is None

    def test_llm_exception_during_invoke_returns_none(self) -> None:
        """RuntimeError during LLM invoke must return None, not raise."""
        session = _fresh_session()
        mock_client = MagicMock()
        mock_client.invoke.side_effect = RuntimeError("timeout")
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=mock_client,
        ):
            decision = classify_intent_with_llm("something happened", session)
        assert decision is None

    def test_result_is_cached(self) -> None:
        """The LLM should only be called once for identical (text, has_prior) pairs."""
        session = _fresh_session()
        mock_client = _mock_llm_response("cli_agent")
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=mock_client,
        ):
            classify_intent_with_llm("run synthetic test 001", session)
            classify_intent_with_llm("run synthetic test 001", session)
        assert mock_client.invoke.call_count == 1

    def test_cache_key_includes_prior_state(self) -> None:
        """Same text with and without prior state should produce distinct calls."""
        session_no_prior = _fresh_session()
        session_with_prior = _fresh_session(with_prior_state=True)
        mock_client = _mock_llm_response("cli_agent")
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=mock_client,
        ):
            classify_intent_with_llm("why?", session_no_prior)
            classify_intent_with_llm("why?", session_with_prior)
        # Two distinct cache keys → two LLM calls.
        assert mock_client.invoke.call_count == 2

    def test_confidence_is_set_on_llm_decision(self) -> None:
        session = _fresh_session()
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("cli_agent"),
        ):
            decision = classify_intent_with_llm("run synthetic test 003-cpu-spike", session)
        assert decision is not None
        assert 0.0 < decision.confidence <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Integration tests: route_input() with mocked LLM
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteInputWithLLM:
    """Tests for the full route_input pipeline, exercising the LLM path."""

    @pytest.fixture(autouse=True)
    def _enable_llm_routing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-enable the LLM routing pipeline for this test class.

        The conftest disables LLM routing by default (to prevent live LLM
        calls in unrelated tests).  Tests in this class need the LLM branch
        of ``route_input`` to be active so they can verify that the mocked
        ``classify_intent_with_llm`` is actually invoked.
        """
        monkeypatch.setattr("app.cli.interactive_shell.router._LLM_ROUTING_DISABLED", False)

    def _patch_llm_classifier(self, route_kind: str) -> MagicMock:
        """Return a mock for ``classify_intent_with_llm`` returning *route_kind*."""
        mock = MagicMock(
            return_value=RouteDecision(
                route_kind=RouteKind(route_kind),
                confidence=0.88,
                matched_signals=("llm_intent_classifier",),
            )
        )
        return mock

    def test_synthetic_test_with_connection_in_id_routes_cli_agent(self) -> None:
        """'run synthetic test 002-connection-exhaustion' must reach cli_agent.

        This was the specific failure: 'connection' is an alert-signal word in
        the regex ruleset, causing the request to be misrouted to new_alert.
        The LLM understands context and correctly classifies it as cli_agent.
        """
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("cli_agent"),
        ):
            decision = route_input("run synthetic test 002-connection-exhaustion", session)
        assert decision.route_kind == RouteKind.CLI_AGENT
        assert decision.matched_signals == ("llm_intent_classifier",)

    def test_synthetic_test_with_memory_in_id_routes_cli_agent(self) -> None:
        """'memory' is an alert signal; LLM must not be fooled by it in an ID."""
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("cli_agent"),
        ):
            decision = route_input("run synthetic test 005-memory-pressure", session)
        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_synthetic_test_with_cpu_in_id_routes_cli_agent(self) -> None:
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("cli_agent"),
        ):
            decision = route_input("launch synthetic test 003-cpu-spike", session)
        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_real_alert_routes_new_alert_via_llm(self) -> None:
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("new_alert"),
        ):
            decision = route_input(
                "the checkout service is returning 502 errors for 30% of requests", session
            )
        assert decision.route_kind == RouteKind.NEW_ALERT

    def test_follow_up_with_prior_state_via_llm(self) -> None:
        session = _fresh_session(with_prior_state=True)
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("follow_up"),
        ):
            decision = route_input("what caused the spike?", session)
        assert decision.route_kind == RouteKind.FOLLOW_UP

    def test_cli_help_via_llm(self) -> None:
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            self._patch_llm_classifier("cli_help"),
        ):
            decision = route_input("how do I set up the datadog integration?", session)
        assert decision.route_kind == RouteKind.CLI_HELP

    def test_slash_command_bypasses_llm(self) -> None:
        """Slash commands must be handled by the deterministic fast-path, never LLM."""
        session = _fresh_session()
        mock_llm = MagicMock()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            mock_llm,
        ):
            decision = route_input("/help", session)
        mock_llm.assert_not_called()
        assert decision.route_kind == RouteKind.SLASH

    def test_bare_alias_bypasses_llm(self) -> None:
        """Bare command aliases (e.g. 'help') must be handled by fast-path."""
        session = _fresh_session()
        mock_llm = MagicMock()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            mock_llm,
        ):
            decision = route_input("help", session)
        mock_llm.assert_not_called()
        assert decision.route_kind == RouteKind.SLASH

    def test_llm_none_falls_back_to_regex(self) -> None:
        """When LLM returns None (unavailable), the regex rules must still route correctly."""
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=None,
        ):
            # "how do I run an investigation?" matches a CLI help regex.
            decision = route_input("how do I run an investigation?", session)
        assert decision.route_kind == RouteKind.CLI_HELP

    def test_llm_none_regex_fallback_new_alert(self) -> None:
        """Without LLM, alert-signal regex still routes real alerts to new_alert."""
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=None,
        ):
            decision = route_input("CPU spiked on orders-api", session)
        assert decision.route_kind == RouteKind.NEW_ALERT

    def test_llm_decision_confidence_is_honoured(self) -> None:
        """The RouteDecision returned by the LLM must flow through unmodified."""
        session = _fresh_session()
        llm_decision = RouteDecision(
            route_kind=RouteKind.CLI_AGENT,
            confidence=0.91,
            matched_signals=("llm_intent_classifier",),
        )
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=llm_decision,
        ):
            decision = route_input("run synthetic benchmark", session)
        assert decision.confidence == 0.91
        assert decision.matched_signals == ("llm_intent_classifier",)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fallback-only tests: LLM disabled, regression test for all regex cases
# ─────────────────────────────────────────────────────────────────────────────


class TestRegexFallbackRouting:
    """Ensure the regex rules still work correctly when the LLM is disabled.

    These tests run with ``OPENSRE_DISABLE_LLM_ROUTING=1`` (via the fixture)
    so they exercise only the pure regex path — useful as a regression guard
    for the offline / test environment.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            # ── slash ──────────────────────────────────────────────────────
            ("/help", "slash"),
            ("  /status  ", "slash"),
            ("/investigate alert.json", "slash"),
            # ── bare aliases ───────────────────────────────────────────────
            ("help", "slash"),
            ("exit", "slash"),
            ("quit", "slash"),
            ("status", "slash"),
            ("clear", "slash"),
            ("reset", "slash"),
            ("?", "slash"),
            ("HELP", "slash"),  # case-insensitive
            ("hlep", "slash"),  # typo-tolerance
            # ── cli_help ───────────────────────────────────────────────────
            ("how do I run an investigation?", "cli_help"),
            ("what command do I use?", "cli_help"),
            ("how do I configure datadog?", "cli_help"),
            ("how to deploy OpenSRE on Railway?", "cli_help"),
            ("does opensre support honeycomb?", "cli_help"),
            ("can opensre integrate with bitbucket?", "cli_help"),
            ("what are the supported integrations?", "cli_help"),
            ("check the docs for datadog setup", "cli_help"),
            ("what does opensre onboard do?", "cli_help"),
            # ── new_alert ──────────────────────────────────────────────────
            ("why is the database slow?", "new_alert"),
            ("CPU spiked on orders-api", "new_alert"),
            ('{"alertname": "HighCPU", "severity": "critical"}', "new_alert"),
            ("the checkout API returns 502s for 15% of requests since 14:00 UTC", "new_alert"),
            # ── cli_agent ──────────────────────────────────────────────────
            ("run syntehtic test 002-connection-exhaustion", "cli_agent"),  # typo
            ("show me connected services", "cli_agent"),
            ("try a sample alert", "cli_agent"),
            ("check the health of my opensre and then show me all connected services", "cli_agent"),
            ("please connect to local llama", "cli_agent"),
        ],
    )
    def test_regex_fallback_canonical_cases(
        self,
        text: str,
        expected: str,
        _disable_llm_routing: None,
    ) -> None:
        session = _fresh_session()
        decision = route_input(text, session)
        assert decision.route_kind.value == expected, (
            f"Expected {expected!r} for {text!r}, got {decision.route_kind.value!r}"
        )

    def test_prior_state_short_follow_up(self, _disable_llm_routing: None) -> None:
        session = _fresh_session(with_prior_state=True)
        assert route_input("why?", session).route_kind == RouteKind.FOLLOW_UP
        assert route_input("what caused it?", session).route_kind == RouteKind.FOLLOW_UP

    def test_prior_state_new_alert_still_routes_correctly(self, _disable_llm_routing: None) -> None:
        session = _fresh_session(with_prior_state=True)
        assert route_input("CPU spiked on orders-api", session).route_kind == RouteKind.NEW_ALERT

    def test_prior_state_small_talk_cli_agent(self, _disable_llm_routing: None) -> None:
        session = _fresh_session(with_prior_state=True)
        assert route_input("thanks", session).route_kind == RouteKind.CLI_AGENT


# ─────────────────────────────────────────────────────────────────────────────
# 4. Edge cases: alert vocabulary in non-alert contexts
# ─────────────────────────────────────────────────────────────────────────────


class TestAlertVocabularyInNonAlertContexts:
    """Guard against false positives from alert-signal words in non-alert input.

    These tests patch the LLM to return the 'correct' answer so that the test
    validates both:
    (a) the LLM is consulted (not short-circuited by regex), and
    (b) the routing result is correct when the LLM gives the right answer.
    """

    @pytest.fixture(autouse=True)
    def _enable_llm_routing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-enable the LLM routing pipeline for this test class."""
        monkeypatch.setattr("app.cli.interactive_shell.router._LLM_ROUTING_DISABLED", False)

    @pytest.mark.parametrize(
        "text,llm_route,expected_kind",
        [
            # "connection" in a synthetic test ID must not trigger new_alert.
            (
                "run synthetic test 002-connection-exhaustion",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "memory" in a test ID.
            (
                "launch synthetic test 005-memory-pressure",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "cpu" in a test ID.
            (
                "start synthetic test 003-high-cpu",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "disk" in a test ID.
            (
                "run synthetic test 007-disk-failure",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "timeout" in a test ID.
            (
                "execute synthetic test 008-timeout-cascade",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "crash" in a test ID.
            (
                "run synthetic test 009-pod-crash",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "error" in a benchmark name.
            (
                "launch benchmark error-injection-test",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "alert" in a sample test launch phrase.
            (
                "run a sample alert",
                "cli_agent",
                RouteKind.CLI_AGENT,
            ),
            # "latency" in a capabilities question.
            (
                "how do I measure latency with opensre?",
                "cli_help",
                RouteKind.CLI_HELP,
            ),
        ],
    )
    def test_alert_vocab_in_non_alert_context(
        self,
        text: str,
        llm_route: str,
        expected_kind: RouteKind,
    ) -> None:
        session = _fresh_session()
        mock_decision = RouteDecision(
            route_kind=RouteKind(llm_route),
            confidence=0.88,
            matched_signals=("llm_intent_classifier",),
        )
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=mock_decision,
        ):
            decision = route_input(text, session)
        assert decision.route_kind == expected_kind, (
            f"Expected {expected_kind!r} for {text!r}, got {decision.route_kind!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. LLM routing disabled via environment variable
# ─────────────────────────────────────────────────────────────────────────────


class TestLLMRoutingDisabledFlag:
    """Verify that OPENSRE_DISABLE_LLM_ROUTING bypasses the LLM entirely."""

    def test_llm_not_called_when_disabled(self, _disable_llm_routing: None) -> None:
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm"
        ) as mock_llm:
            route_input("run synthetic test 002-connection-exhaustion", session)
        mock_llm.assert_not_called()

    def test_llm_not_called_for_any_input_when_disabled(self, _disable_llm_routing: None) -> None:
        session = _fresh_session()
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm"
        ) as mock_llm:
            for text in (
                "why is the database slow?",
                "run synthetic test 001",
                "show me services",
                "how do I configure opensre?",
                "the checkout API is down",
            ):
                route_input(text, session)
        mock_llm.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 6. RouteDecision telemetry / payload tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteDecisionFromLLM:
    """Verify the RouteDecision returned for LLM-classified routes is well-formed."""

    @pytest.fixture(autouse=True)
    def _enable_llm_routing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-enable the LLM routing pipeline for this test class."""
        monkeypatch.setattr("app.cli.interactive_shell.router._LLM_ROUTING_DISABLED", False)

    def test_llm_route_decision_event_payload(self) -> None:
        session = _fresh_session()
        llm_decision = RouteDecision(
            route_kind=RouteKind.CLI_AGENT,
            confidence=0.88,
            matched_signals=("llm_intent_classifier",),
        )
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=llm_decision,
        ):
            decision = route_input("run synthetic test 002-connection-exhaustion", session)

        payload = decision.to_event_payload()
        assert payload["route_kind"] == "cli_agent"
        assert payload["confidence"] == 0.88
        assert "llm_intent_classifier" in payload["matched_signals"]
        assert payload["fallback_reason"] == ""

    def test_llm_route_decision_has_no_fallback_reason(self) -> None:
        session = _fresh_session()
        llm_decision = RouteDecision(
            route_kind=RouteKind.NEW_ALERT,
            confidence=0.88,
            matched_signals=("llm_intent_classifier",),
        )
        with patch(
            "app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm",
            return_value=llm_decision,
        ):
            decision = route_input("502 errors in production", session)
        assert decision.fallback_reason is None


# ─────────────────────────────────────────────────────────────────────────────
# 7. clear_classify_cache utility
# ─────────────────────────────────────────────────────────────────────────────


class TestClearClassifyCache:
    """Verify the cache-clear utility forces a fresh LLM call."""

    def test_cache_cleared_forces_new_call(self) -> None:
        session = _fresh_session()
        mock_client = _mock_llm_response("cli_agent")
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=mock_client,
        ):
            classify_intent_with_llm("run synthetic test 001", session)
            clear_classify_cache()
            classify_intent_with_llm("run synthetic test 001", session)
        assert mock_client.invoke.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 8. Safety behaviours (Greptile P1 + security fixes)
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyBehaviours:
    """Verify the three safety guarantees added after Greptile review."""

    # ── P1: transient LLM failures must not be permanently cached ────────────

    def test_transient_failure_not_cached(self) -> None:
        """A failed LLM call must not block subsequent retries for the same text."""
        session = _fresh_session()
        fail_client = MagicMock()
        fail_client.invoke.side_effect = RuntimeError("network error")
        ok_client = _mock_llm_response("cli_agent")

        with patch("app.services.llm_client.get_llm_for_tools", return_value=fail_client):
            result_1 = classify_intent_with_llm("run test", session)

        with patch("app.services.llm_client.get_llm_for_tools", return_value=ok_client):
            result_2 = classify_intent_with_llm("run test", session)

        assert result_1 is None
        assert result_2 is not None
        assert result_2.route_kind == RouteKind.CLI_AGENT

    def test_none_response_cleared_from_cache(self) -> None:
        """Unparseable LLM response must also be retried on the next call."""
        session = _fresh_session()
        garbage_client = _mock_llm_response("I have no idea")
        ok_client = _mock_llm_response("new_alert")

        with patch("app.services.llm_client.get_llm_for_tools", return_value=garbage_client):
            first = classify_intent_with_llm("orders api 502", session)

        with patch("app.services.llm_client.get_llm_for_tools", return_value=ok_client):
            second = classify_intent_with_llm("orders api 502", session)

        assert first is None
        assert second is not None
        assert second.route_kind == RouteKind.NEW_ALERT

    # ── P1: LLM must not return follow_up without prior state ────────────────

    def test_follow_up_without_prior_state_overridden_to_cli_agent(self) -> None:
        """If LLM returns follow_up but session has no prior state, override to cli_agent."""
        session = _fresh_session(with_prior_state=False)
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("follow_up"),
        ):
            decision = classify_intent_with_llm("why did it fail?", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_follow_up_with_prior_state_allowed(self) -> None:
        """follow_up is only suppressed when session.last_state is None."""
        session = _fresh_session(with_prior_state=True)
        with patch(
            "app.services.llm_client.get_llm_for_tools",
            return_value=_mock_llm_response("follow_up"),
        ):
            decision = classify_intent_with_llm("why did it fail?", session)
        assert decision is not None
        assert decision.route_kind == RouteKind.FOLLOW_UP

    # ── Security: prompt injection via control characters / long input ────────

    def test_long_input_truncated_before_llm_call(self) -> None:
        """Input longer than _MAX_TEXT_LEN must be truncated before entering the prompt."""
        from app.cli.interactive_shell import llm_intent_classifier

        session = _fresh_session()
        long_text = "run test " + "A" * 600
        mock_client = _mock_llm_response("cli_agent")

        with patch("app.services.llm_client.get_llm_for_tools", return_value=mock_client):
            classify_intent_with_llm(long_text, session)

        assert mock_client.invoke.call_count == 1
        prompt_used: str = mock_client.invoke.call_args[0][0]
        assert len(prompt_used) < len(long_text) + len(llm_intent_classifier._SYSTEM_PROMPT) + 100

    def test_control_characters_stripped_before_prompt(self) -> None:
        """Null bytes and escape sequences must be removed before embedding in the prompt."""
        session = _fresh_session()
        injected = "run test\x00\x01\x1b[31mevil\x1b[0m"
        mock_client = _mock_llm_response("cli_agent")

        with patch("app.services.llm_client.get_llm_for_tools", return_value=mock_client):
            decision = classify_intent_with_llm(injected, session)

        assert decision is not None
        prompt_used: str = mock_client.invoke.call_args[0][0]
        assert "\x00" not in prompt_used
        assert "\x01" not in prompt_used
        assert "\x1b" not in prompt_used
