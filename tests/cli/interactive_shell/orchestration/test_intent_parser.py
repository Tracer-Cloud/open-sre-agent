"""Unit tests for natural-language intent parsing helpers."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    SAMPLE_ALERT_RE,
    extract_implementation_request,
    extract_quoted_investigation_request,
    extract_shell_command,
    normalize_shell_command,
    shutil,
    split_prompt_clauses,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    _fail_closed_vague_local_model,
    _finalize_planner_result,
    _reconcile_compound_actions,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_with_unhandled,
)


def test_split_prompt_clauses_preserves_positions() -> None:
    msg = "  check health AND  list services "
    clauses = split_prompt_clauses(msg)
    assert len(clauses) == 2
    assert clauses[0].text == "check health"
    assert clauses[1].text == "list services"
    assert msg.index(clauses[0].text) == clauses[0].position


def test_normalize_shell_command_rejects_multiline() -> None:
    assert normalize_shell_command("ls\npwd") is None


def test_normalize_shell_command_strips_ticks() -> None:
    assert normalize_shell_command("`whoami`") == "whoami"


def test_extract_implementation_request_matches_explicit_implement_phrase() -> None:
    action = extract_implementation_request(
        PromptClause(text="please implement /history search", position=3)
    )

    assert action is not None
    assert action.kind == "implementation"
    assert action.content == "/history search"
    assert action.position == 10


def test_extract_implementation_request_allows_context_dependent_bare_implement() -> None:
    action = extract_implementation_request(PromptClause(text="implement", position=0))

    assert action is not None
    assert action.kind == "implementation"
    assert action.content == "implement"


def test_code_editor_command_is_not_implementation_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _command: "/usr/bin/code")
    clause = PromptClause(text="code .", position=0)

    assert extract_implementation_request(clause) is None
    action = extract_shell_command(clause)
    assert action is not None
    assert action.kind == "shell"
    assert action.content == "code ."


class TestSampleAlertRE:
    """SAMPLE_ALERT_RE is now the single canonical source for sample-alert launch
    detection (shared by both action_planner and the terminal_intent routing
    surface). These fixtures guard against accidental pattern drift."""

    def test_matches_canonical_sample_alert_phrases(self) -> None:
        positives = [
            "try a sample alert",
            "run a sample alert",
            "launch a simple alert",
            "fire a demo alert",
            "start a test alert",
            "send a sample event",
            "trigger a demo event",
            "okay launch a simple alert",
        ]
        for phrase in positives:
            assert SAMPLE_ALERT_RE.search(phrase) is not None, (
                f"SAMPLE_ALERT_RE should match: {phrase!r}"
            )

    def test_does_not_match_real_incident_descriptions(self) -> None:
        negatives = [
            "the checkout API returned a 502 error",
            "CPU spiked on orders-api",
            "why is the database slow?",
            "investigate the latency spike",
        ]
        for phrase in negatives:
            assert SAMPLE_ALERT_RE.search(phrase) is None, (
                f"SAMPLE_ALERT_RE should NOT match: {phrase!r}"
            )


def test_extract_quoted_investigation_request_matches_bare_investigate_verb() -> None:
    action = extract_quoted_investigation_request(
        PromptClause(text='investigate "hello world"', position=0)
    )
    assert action is not None
    assert action.kind == "investigation"
    assert action.content == "hello world"


def test_map_actions_with_unhandled_remote_then_investigate_compound() -> None:
    actions, has_unhandled = map_actions_with_unhandled(
        'run /remote and then investigate "hello world"'
    )
    assert not has_unhandled
    assert [(item.kind, item.content) for item in actions] == [
        ("slash", "/remote"),
        ("investigation", "hello world"),
    ]


def test_map_actions_with_unhandled_health_then_connected_services() -> None:
    actions, has_unhandled = map_actions_with_unhandled(
        "check the health of my opensre and then show me all connected services"
    )
    assert not has_unhandled
    assert [(item.kind, item.content) for item in actions] == [
        ("slash", "/health"),
        ("slash", "/list integrations"),
    ]


def test_reconcile_compound_actions_relabels_source_as_llm() -> None:
    llm_actions = [
        PlannedAction(
            kind="slash", content="/health", position=0, source="llm", target_surface="slash"
        )
    ]
    actions, _has_unhandled = _reconcile_compound_actions(
        "run /health and then trigger a sample alert investigation",
        llm_actions,
        False,
    )
    assert len(actions) == 2
    assert all(action.source == "llm" for action in actions)


def test_fail_closed_vague_local_llama_connect() -> None:
    result = _fail_closed_vague_local_model("please connect to local llama")
    assert result == ([], True)


def test_finalize_preserves_handoff_for_checkout_http_errors() -> None:
    handoff = [
        PlannedAction(
            kind="assistant_handoff",
            content="incident_description:checkout_502_rate",
            position=0,
            source="llm",
        )
    ]
    actions, has_unhandled = _finalize_planner_result(
        "the checkout service is returning 502 errors for 30% of requests",
        handoff,
        True,
    )
    assert has_unhandled is True
    assert len(actions) == 1
    assert actions[0].kind == "assistant_handoff"


def test_finalize_coerces_investigation_to_handoff_for_checkout_http_errors() -> None:
    investigation = [
        PlannedAction(
            kind="investigation",
            content="the checkout service is returning 502 errors for 30% of requests",
            position=0,
            source="llm",
            target_surface="investigation",
            args={"alert_text": "the checkout service is returning 502 errors for 30% of requests"},
        )
    ]
    actions, has_unhandled = _finalize_planner_result(
        "the checkout service is returning 502 errors for 30% of requests",
        investigation,
        False,
    )
    assert has_unhandled is False
    assert len(actions) == 1
    assert actions[0].kind == "assistant_handoff"


def test_finalize_coerces_rich_paste_investigation_without_unhandled_flag() -> None:
    message = (
        "Checkout API is returning HTTP 500s for 30% of requests since 14:05 UTC.\n"
        "Service: checkout-api\n"
        "Region: us-east-1"
    )
    investigation = [
        PlannedAction(
            kind="investigation",
            content=message,
            position=0,
            source="llm",
            target_surface="investigation",
        )
    ]
    actions, has_unhandled = _finalize_planner_result(message, investigation, False)
    assert has_unhandled is False
    assert len(actions) == 1
    assert actions[0].kind == "assistant_handoff"


def test_finalize_upgrades_handoff_to_investigation_for_cpu_spike() -> None:
    handoff = [
        PlannedAction(
            kind="assistant_handoff",
            content="diagnostic_question:cpu",
            position=0,
            source="llm",
        )
    ]
    actions, has_unhandled = _finalize_planner_result(
        "CPU is spiking to 99% on the orders-api pods",
        handoff,
        False,
    )
    assert has_unhandled is False
    assert len(actions) == 1
    assert actions[0].kind == "investigation"
