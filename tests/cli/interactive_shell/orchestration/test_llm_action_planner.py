"""Tests for the LLM-backed structured action planner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner as planner_module
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)


def _make_llm_response(
    actions: list[dict],
    unhandled_text: str = "",
) -> str:
    return json.dumps({"actions": actions, "unhandled_text": unhandled_text})


def _fake_client(response_text: str) -> MagicMock:
    response = MagicMock()
    response.content = response_text
    client = MagicMock()
    client.invoke.return_value = response
    return client


def test_render_system_prompt_preserves_literal_json_shape() -> None:
    prompt = planner_module._render_system_prompt(
        slash_commands="/health, /help",
        synthetic_scenarios="001-replication-lag",
    )
    assert '"actions": [' in prompt
    assert '{"actions": [], "unhandled_text": "<original message>"}' in prompt
    assert "command name must be one of: /health, /help" in prompt
    assert "scenario-id is one of: 001-replication-lag" in prompt


# ---------------------------------------------------------------------------
# Happy-path: valid JSON plan
# ---------------------------------------------------------------------------


def test_valid_plan_returns_llm_sourced_planned_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [
            {
                "kind": "slash",
                "content": "/health",
                "confidence": 0.95,
                "rationale": "user asked for health check",
            }
        ]
    )
    monkeypatch.setattr(
        planner_module,
        "_call_llm",
        lambda _text: payload,
    )

    result = planner_module.plan_actions_with_llm("check health")
    assert result is not None
    actions, has_unhandled = result
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, PlannedAction)
    assert action.kind == "slash"
    assert action.content == "/health"
    assert action.source == "llm"
    assert action.confidence == pytest.approx(0.95)
    assert action.target_surface == "slash"
    assert action.rationale == "user asked for health check"
    assert has_unhandled is False


def test_valid_plan_multiple_actions_ordered_by_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _make_llm_response(
        [
            {"kind": "slash", "content": "/version", "confidence": 0.9, "rationale": "version"},
            {
                "kind": "investigation",
                "content": "high cpu",
                "confidence": 0.8,
                "rationale": "investigate",
            },
        ]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("show version and investigate high cpu")
    assert result is not None
    actions, _ = result
    assert [a.kind for a in actions] == ["slash", "investigation"]
    assert all(a.source == "llm" for a in actions)


def test_target_surface_set_correctly_for_each_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        ("slash", "/help", "slash"),
        ("llm_provider", "slash"),
        ("task_cancel", "slash"),
        ("shell", "terminal"),
        ("cli_command", "terminal"),
        ("implementation", "implementation"),
        ("investigation", "investigation"),
        ("synthetic_test", "rds_postgres:001-replication-lag", "investigation"),
        ("sample_alert", "investigation"),
    ]
    for case in cases:
        if len(case) == 3:
            kind, content, expected_surface = case
        else:
            kind, expected_surface = case
            content = "x"
        payload = _make_llm_response(
            [{"kind": kind, "content": content, "confidence": 0.9, "rationale": "r"}]
        )
        monkeypatch.setattr(planner_module, "_call_llm", lambda _text, p=payload: p)
        result = planner_module.plan_actions_with_llm("test")
        assert result is not None
        actions, _ = result
        assert len(actions) == 1
        assert actions[0].target_surface == expected_surface, (
            f"kind={kind!r}: expected surface {expected_surface!r}, got {actions[0].target_surface!r}"
        )


# ---------------------------------------------------------------------------
# Invalid kind: dropped from results
# ---------------------------------------------------------------------------


def test_invalid_kind_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [
            {"kind": "not_a_real_kind", "content": "whatever", "confidence": 0.9, "rationale": ""},
            {"kind": "slash", "content": "/health", "confidence": 0.9, "rationale": ""},
        ]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("do stuff")
    assert result is not None
    actions, _ = result
    assert len(actions) == 1
    assert actions[0].kind == "slash"


def test_all_invalid_kinds_returns_empty_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [{"kind": "unknown_kind", "content": "something", "confidence": 0.9, "rationale": ""}]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("something")
    assert result is not None
    actions, _ = result
    assert actions == []


# ---------------------------------------------------------------------------
# Low confidence: dropped from results
# ---------------------------------------------------------------------------


def test_low_confidence_action_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [
            {"kind": "slash", "content": "/health", "confidence": 0.4, "rationale": ""},
            {"kind": "slash", "content": "/version", "confidence": 0.8, "rationale": ""},
        ]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("something")
    assert result is not None
    actions, _ = result
    assert len(actions) == 1
    assert actions[0].content == "/version"


# ---------------------------------------------------------------------------
# Malformed / invalid JSON: returns None
# ---------------------------------------------------------------------------


def test_malformed_json_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: "this is not json {{{")

    result = planner_module.plan_actions_with_llm("check health")
    assert result is None


def test_json_missing_actions_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        planner_module,
        "_call_llm",
        lambda _text: json.dumps({"unhandled_text": "oops"}),
    )

    result = planner_module.plan_actions_with_llm("check health")
    assert result is None


def test_json_not_a_dict_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        planner_module,
        "_call_llm",
        lambda _text: json.dumps(["slash", "/health"]),
    )

    result = planner_module.plan_actions_with_llm("check health")
    assert result is None


# ---------------------------------------------------------------------------
# LLM call failure: returns None
# ---------------------------------------------------------------------------


def test_llm_call_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: None)

    result = planner_module.plan_actions_with_llm("check health")
    assert result is None


def test_llm_import_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the LLM client module cannot be imported _call_llm returns None."""

    def _broken_import(text: str) -> str | None:
        return None

    monkeypatch.setattr(planner_module, "_call_llm", _broken_import)
    assert planner_module.plan_actions_with_llm("anything") is None


# ---------------------------------------------------------------------------
# Unhandled text flag
# ---------------------------------------------------------------------------


def test_unhandled_text_sets_has_unhandled_true(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [{"kind": "slash", "content": "/health", "confidence": 0.9, "rationale": ""}],
        unhandled_text="and tell me a joke",
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("check health and tell me a joke")
    assert result is not None
    _, has_unhandled = result
    assert has_unhandled is True


def test_empty_unhandled_text_sets_has_unhandled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [{"kind": "slash", "content": "/health", "confidence": 0.9, "rationale": ""}],
        unhandled_text="",
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("check health")
    assert result is not None
    _, has_unhandled = result
    assert has_unhandled is False


# ---------------------------------------------------------------------------
# Content sanitisation
# ---------------------------------------------------------------------------


def test_content_is_clamped_to_max_length(monkeypatch: pytest.MonkeyPatch) -> None:
    long_content = "x" * 500
    payload = _make_llm_response(
        [{"kind": "investigation", "content": long_content, "confidence": 0.9, "rationale": ""}]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("do something long")
    assert result is not None
    actions, _ = result
    assert len(actions[0].content) == planner_module._MAX_CONTENT_LEN


def test_slash_action_with_unknown_command_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _make_llm_response(
        [{"kind": "slash", "content": "/not-a-command", "confidence": 0.9, "rationale": ""}]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("do unknown thing")
    assert result is not None
    actions, _ = result
    assert actions == []


def test_synthetic_action_with_unknown_scenario_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        planner_module,
        "_list_synthetic_scenarios",
        lambda: ("001-replication-lag",),
    )
    payload = _make_llm_response(
        [
            {
                "kind": "synthetic_test",
                "content": "rds_postgres:999-missing",
                "confidence": 0.9,
                "rationale": "",
            }
        ]
    )
    monkeypatch.setattr(planner_module, "_call_llm", lambda _text: payload)

    result = planner_module.plan_actions_with_llm("run synthetic test 999")
    assert result is not None
    actions, _ = result
    assert actions == []
