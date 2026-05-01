"""Tests for REPL input classification."""

from __future__ import annotations

from app.cli.interactive_shell.router import classify_input
from app.cli.interactive_shell.session import ReplSession


class TestClassifyInput:
    def test_slash_command(self) -> None:
        session = ReplSession()
        assert classify_input("/help", session) == "slash"
        assert classify_input("  /status", session) == "slash"

    def test_bare_command_word_classified_as_slash(self) -> None:
        session = ReplSession()
        # A bare word matching a slash command short name should route to slash
        # even without the leading '/' and even with no prior investigation.
        for word in ("help", "exit", "quit", "status", "clear", "reset", "trust"):
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

    def test_no_prior_greeting_routes_to_cli_agent_not_investigation(self) -> None:
        session = ReplSession()
        assert classify_input("hey", session) == "cli_agent"
        assert classify_input("hi", session) == "cli_agent"
        assert classify_input("hello", session) == "cli_agent"

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

    def test_long_integration_question_stays_cli_agent(self) -> None:
        """Integration inventory/capability questions are terminal work, not alerts."""
        session = ReplSession()
        text = (
            "tell me about what the discord integration can do and then tell me what "
            "datadog services I have connections to"
        )

        assert len(text) >= 48
        assert classify_input(text, session) == "cli_agent"

    def test_connection_substring_in_connections_is_not_alert_signal(self) -> None:
        session = ReplSession()

        assert classify_input("what datadog connections do I have?", session) == "cli_agent"

    def test_no_prior_state_incident_question_is_new_alert(self) -> None:
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
