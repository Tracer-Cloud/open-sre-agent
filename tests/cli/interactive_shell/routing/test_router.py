"""Tests for REPL input classification."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.cli.interactive_shell.routing import router as _router_module
from app.cli.interactive_shell.routing.router import RouteKind, classify_input, route_input
from app.cli.interactive_shell.runtime.session import ReplSession

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class TestClassifyInput:
    def test_slash_command(self) -> None:
        session = ReplSession()
        assert classify_input("/help", session) == "slash"
        assert classify_input("  /status", session) == "slash"

    def test_bare_command_word_classified_as_slash(self) -> None:
        session = ReplSession()
        # A bare word matching a slash command short name should route to slash
        # even without the leading '/' and even with no prior investigation.
        for word in ("help", "exit", "quit", "status", "clear", "reset", "trust", "welcome"):
            assert classify_input(word, session) == "slash", word

    def test_bare_question_mark_is_slash(self) -> None:
        """Typing `?` at the prompt should route to /help, not be mistaken for
        a new alert or a follow-up."""
        session = ReplSession()
        assert classify_input("?", session) == "slash"
        assert classify_input("  ?  ", session) == "slash"
        # Even with prior investigation state, bare `?` is the help shortcut —
        # not a short follow-up question.
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("?", session) == "slash"

    def test_bare_command_is_case_insensitive(self) -> None:
        session = ReplSession()
        assert classify_input("HELP", session) == "slash"
        assert classify_input("Exit", session) == "slash"

    def test_no_prior_greeting_routes_to_welcome_panel(self) -> None:
        # Greetings and meta-words ("hi", "agent", "menu", …) are aliased to the
        # /welcome slash command so the user always lands on the structured
        # welcome panel instead of an unstructured LLM reply.
        session = ReplSession()
        for word in ("hey", "hi", "agent", "menu", "welcome"):
            assert classify_input(word, session) == "slash", word

    def test_long_operational_health_question_stays_cli_agent(self) -> None:
        """Long setup questions must not hit LangGraph just because len >= 48."""
        session = ReplSession()
        text = "check the health of my opensre and then show me all connected services"
        assert len(text) >= 48
        assert classify_input(text, session) == "cli_agent"

    def test_local_llama_connect_stays_cli_agent_with_prior_state(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}

        assert classify_input("please connect to local llama", session) == "cli_agent"

    def test_long_integration_question_stays_cli_agent(self, monkeypatch) -> None:
        """Integration inventory/capability questions are terminal work, not alerts."""
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()
        text = (
            "tell me about what the discord integration can do and then tell me what "
            "datadog services I have connections to"
        )

        assert len(text) >= 48
        assert classify_input(text, session) == "cli_agent"

    def test_connection_substring_in_connections_is_not_alert_signal(self, monkeypatch) -> None:
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()

        assert classify_input("what datadog connections do I have?", session) == "cli_agent"

    def test_no_prior_state_incident_question_is_new_alert(self, monkeypatch) -> None:
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()
        assert classify_input("why is the database slow?", session) == "new_alert"

    def test_sample_alert_launch_routes_to_cli_agent(self) -> None:
        session = ReplSession()
        assert classify_input("okay launch a simple alert", session) == "cli_agent"
        assert classify_input("try a sample alert", session) == "cli_agent"

    def test_no_prior_long_line_is_new_alert(self) -> None:
        session = ReplSession()
        long_text = "the checkout API returns 502s for 15% of requests since 14:00 UTC"
        assert len(long_text) >= 48
        assert classify_input(long_text, session) == "new_alert"

    def test_no_prior_state_cli_help_patterns(self) -> None:
        session = ReplSession()
        assert classify_input("How do I run an investigation?", session) == "cli_help"
        assert classify_input("what command do I use for investigate?", session) == "cli_help"
        assert classify_input("which command should I use?", session) == "cli_help"
        assert classify_input("what does opensre onboard do?", session) == "cli_help"

    def test_cli_help_takes_priority_over_follow_up(self) -> None:
        """Procedural questions must not be grounded on the last investigation."""
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("How do I run an investigation?", session) == "cli_help"

    def test_short_question_with_prior_state_is_follow_up(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("why?", session) == "follow_up"
        assert classify_input("what caused it?", session) == "follow_up"

    def test_alert_keywords_with_prior_state_still_new_alert(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("CPU spiked on orders-api", session) == "new_alert"
        assert classify_input("5xx errors from checkout service", session) == "new_alert"

    def test_short_question_with_alert_keyword_is_follow_up(self) -> None:
        # Short question-shape wins over the presence of an alert keyword —
        # "why did CPU spike?" should answer from last_state, not kick off a
        # fresh investigation.
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("why did CPU spike?", session) == "follow_up"
        assert classify_input("what caused the memory error?", session) == "follow_up"
        assert classify_input("how did the connection drop?", session) == "follow_up"

    def test_long_non_question_defaults_to_new_alert(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        long_text = (
            "the orders-api service started returning intermittent failures "
            "around 14:00 UTC today and our on-call is paged"
        )
        assert classify_input(long_text, session) == "new_alert"

    def test_long_question_is_still_new_alert(self) -> None:
        # A long incident description phrased as a question should not be
        # mistaken for a follow-up — only short question-shaped input gets
        # the follow-up routing.
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        long_question = (
            "CPU usage on orders-api has been climbing steadily for the past "
            "two hours and we just paged the on-call engineer — what changed?"
        )
        assert classify_input(long_question, session) == "new_alert"

    def test_prior_state_small_talk_routes_to_cli_agent(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("thanks", session) == "cli_agent"
        assert classify_input("ok cool", session) == "cli_agent"

    def test_documentation_style_questions_route_to_cli_help(self) -> None:
        """Docs-style how-to questions must route to the documentation-aware
        cli_help handler (#1166), not be mistaken for incidents or chat."""
        session = ReplSession()
        # Configuration / setup questions for an integration.
        assert classify_input("How do I configure Datadog?", session) == "cli_help"
        assert classify_input("how do i set up grafana", session) == "cli_help"
        assert classify_input("how to integrate with slack", session) == "cli_help"
        # Deployment questions.
        assert classify_input("how do I deploy this?", session) == "cli_help"
        assert classify_input("How to deploy OpenSRE on Railway?", session) == "cli_help"
        # Generic docs / feature inventory questions.
        assert (
            classify_input("what does the documentation say about masking?", session) == "cli_help"
        )
        assert classify_input("does opensre support honeycomb?", session) == "cli_help"
        assert classify_input("can opensre integrate with bitbucket?", session) == "cli_help"
        assert classify_input("what are the supported integrations?", session) == "cli_help"
        # Explicit references to the docs route to docs-grounded help.
        assert classify_input("check the docs for datadog setup", session) == "cli_help"
        assert classify_input("according to the docs, what env do I need?", session) == "cli_help"

    def test_incident_text_mentioning_docs_still_routes_to_new_alert(self) -> None:
        """The bare word 'docs' inside an incident description must NOT be
        mistaken for a documentation question (#1166). An incident narrative
        about a service named 'docs' should still run LangGraph investigation."""
        session = ReplSession()
        text = (
            "the database docs service started returning 502 errors at 14:00 UTC "
            "for 25% of requests"
        )
        assert classify_input(text, session) == "new_alert"

    def test_short_incident_with_in_docs_phrase_routes_to_new_alert(self) -> None:
        """'in (the) docs' on its own is too broad to be a help signal — an
        incident description that mentions errors happening "in docs" must
        still reach the investigation pipeline (#1166 review feedback).

        Only counts as a docs question when the surrounding clause is
        question-shaped (covered by ``test_in_the_docs_question_routes_to_cli_help``).
        """
        session = ReplSession()
        text = "the API errors are happening in docs"
        assert classify_input(text, session) == "new_alert"

    def test_in_the_docs_question_routes_to_cli_help(self) -> None:
        """The "in (the) docs" phrasing IS a docs signal when the surrounding
        clause is question-shaped — verifies the targeted pattern still
        catches legitimate docs questions (#1166)."""
        session = ReplSession()
        assert classify_input("in the docs, where is the OAuth flow?", session) == "cli_help"

    def test_route_input_returns_structured_decision(self) -> None:
        session = ReplSession()

        decision = route_input("/help", session)

        assert decision.route_kind == RouteKind.SLASH
        assert decision.confidence == 1.0
        assert decision.matched_signals == ("slash_prefix",)
        assert decision.fallback_reason is None

    def test_route_input_preserves_legacy_classification(self) -> None:
        session = ReplSession()

        cases = [
            "/help",
            "help",
            "how do I run an investigation?",
            "run a sample alert",
            "api latency spiked and 5xx errors increased",
            "hello",
        ]

        for text in cases:
            assert route_input(text, session).route_kind.value == classify_input(text, session)

    def test_route_input_emits_fallback_reason_for_low_signal_input(self, monkeypatch) -> None:
        # Set env var so this test exercises the deterministic regex fallback
        # rather than the LLM path, which would produce different confidence/signals.
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()

        decision = route_input("hello", session)

        assert decision.route_kind == RouteKind.CLI_AGENT
        assert decision.confidence == 0.45
        assert decision.matched_signals == ()
        assert decision.fallback_reason == "no_prior_investigation_and_no_incident_signal"

    def test_cli_action_plan_routes_to_cli_agent_before_investigation(self) -> None:
        session = ReplSession()

        decision = route_input("show me connected services", session)

        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_typoed_synthetic_prompt_routes_to_cli_agent_action_plan(self) -> None:
        session = ReplSession()

        decision = route_input("run syntehtic test 002-connection-exhaustion", session)

        assert decision.route_kind == RouteKind.CLI_AGENT

    def test_remote_deployment_inventory_questions_route_to_cli_agent(self) -> None:
        session = ReplSession()

        for text in (
            "Which remote deployments are connected?",
            "Which remote's deployments are connected?",
            "What remote deployments are connected?",
            "show remote deployments",
            "list remote deployments",
        ):
            decision = route_input(text, session)
            assert decision.route_kind == RouteKind.CLI_AGENT, text

    def test_normal_informational_questions_do_not_start_investigations(self, monkeypatch) -> None:
        # Use the deterministic regex path to lock down the regex rules that
        # prevent informational questions from leaking into new_alert routing.
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()

        for text in (
            "Which deployment options are available?",
            "What deployment environments do I have?",
            "Which clusters are configured?",
            "What nodes are available?",
            "Which services can I connect?",
            "What is a replica?",
            "How many deployments are configured?",
        ):
            assert classify_input(text, session) == "cli_agent", text

    def test_typoed_help_bare_alias_routes_to_slash(self) -> None:
        session = ReplSession()
        assert classify_input("hlep", session) == "slash"

    def test_docs_and_capability_questions_with_incident_vocab_avoid_investigation(self) -> None:
        session = ReplSession()

        for text in (
            "What does OpenSRE deployment support?",
            "Can OpenSRE deploy to a cluster?",
            "Does OpenSRE support node-level logs?",
        ):
            assert classify_input(text, session) == "cli_help", text

    def test_yaml_routing_regression_cases(self) -> None:
        fixture_path = FIXTURES_DIR / "routing_cases.yml"
        payload = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))

        session = ReplSession()

        for case in payload:
            decision = route_input(case["input"], session)

            assert decision.route_kind.value == case["expected"]

    def test_route_input_matched_signals_are_internal_rule_names(self, monkeypatch) -> None:
        # Disable LLM routing so this test exercises the deterministic regex
        # path and can assert on the exact signal name emitted by that path.
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()

        decision = route_input(
            "api latency spiked and 5xx errors increased",
            session,
        )

        assert decision.matched_signals == ("investigation_request",)
        assert "api latency" not in decision.matched_signals


class TestEdgeCaseRegressionFixtures:
    """Regression fixtures for historical boundary prompts that were ambiguous
    between the sample-alert launch surface (cli_agent) and real incident
    investigation (new_alert) during the typed-routing migration (#1375/#1378).

    These fixtures guard the consolidated single-source routing so that the
    boundary does not silently drift if the canonical SAMPLE_ALERT_RE pattern
    in intent_parser is ever edited.
    """

    def test_sample_alert_verb_variants_stay_cli_agent(self) -> None:
        """All verb forms that launch a built-in test alert must route to
        cli_agent, not kick off a real LangGraph investigation."""
        session = ReplSession()
        for phrase in (
            "try a sample alert",
            "run a sample alert",
            "launch a simple alert",
            "fire a demo alert",
            "start a test alert",
            "send a sample event",
            "trigger a demo event",
            "okay launch a simple alert",
            "try a test event",
        ):
            result = classify_input(phrase, session)
            assert result == "cli_agent", (
                f"Expected cli_agent for sample-alert phrase {phrase!r}, got {result!r}"
            )

    def test_real_alert_keywords_alongside_sample_phrasing_still_route_to_new_alert(
        self,
    ) -> None:
        """When a prompt contains alert signal vocabulary alongside a sample-alert
        phrase, the alert signal wins and the turn goes to new_alert."""
        session = ReplSession()
        # "errors" is an alert signal — even though "sample" appears, the
        # investigation pipeline should handle genuine incident descriptions.
        assert classify_input("500 errors happening — run a sample check?", session) == "new_alert"

    def test_prior_state_sample_alert_launch_stays_cli_agent(self) -> None:
        """With a prior investigation present, sample-alert launch must still
        route to cli_agent and not be misclassified as a follow-up question."""
        session = ReplSession()
        session.last_state = {"root_cause": "disk full"}
        assert classify_input("try a sample alert", session) == "cli_agent"
        assert classify_input("launch a simple alert", session) == "cli_agent"

    def test_json_alert_payload_is_new_alert_not_cli_agent(self) -> None:
        """A valid JSON object that looks like an alert payload must route to
        new_alert regardless of surrounding session state."""
        session = ReplSession()
        json_alert = '{"alertname": "HighCPU", "severity": "critical", "service": "checkout"}'
        assert classify_input(json_alert, session) == "new_alert"

    def test_short_incident_question_without_prior_state_is_new_alert(self, monkeypatch) -> None:
        """Short production-symptom questions with no prior investigation must
        reach the LangGraph pipeline, not the cli_agent.  Pinned to the regex
        path so the test is deterministic regardless of LLM availability."""
        monkeypatch.setattr(_router_module, "_LLM_ROUTING_DISABLED", True)
        session = ReplSession()
        for phrase in (
            "why is the database slow?",
            "why is the pod failing?",
            "why is the node timing out?",
        ):
            result = classify_input(phrase, session)
            assert result == "new_alert", (
                f"Expected new_alert for incident question {phrase!r}, got {result!r}"
            )
