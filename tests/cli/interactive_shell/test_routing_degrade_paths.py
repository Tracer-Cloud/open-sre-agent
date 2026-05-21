from __future__ import annotations

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent import evaluator as _evaluator
from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
    ParseError,
    PlannerUnavailable,
    PolicyError,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands import (
    deterministic_action_mapper as _mapper,
)
from app.cli.interactive_shell.routing.types import RouteKind
from app.cli.interactive_shell.runtime.session import ReplSession


@pytest.mark.parametrize(
    ("exc", "expected_tag"),
    [
        (ParseError("parse exploded"), ParseError.reason_tag),
        (PolicyError("policy exploded"), PolicyError.reason_tag),
        (PlannerUnavailable("planner exploded"), PlannerUnavailable.reason_tag),
    ],
)
def test_handle_message_with_agent_reports_typed_degrade_paths(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    expected_tag: str,
) -> None:
    reported: list[dict[str, object]] = []

    def _raise_typed(_text: str) -> tuple[list[object], bool]:
        raise exc

    def _capture(
        _exc: BaseException,
        *,
        context: str,
        extra: dict[str, object] | None = None,
        expected: bool = False,
    ) -> bool:
        reported.append(
            {
                "context": context,
                "extra": dict(extra or {}),
                "expected": expected,
            }
        )
        return True

    monkeypatch.setattr(_evaluator, "map_actions_with_unhandled", _raise_typed)
    monkeypatch.setattr(_evaluator, "report_exception", _capture)

    decision = _evaluator.handle_message_with_agent("please investigate this", ReplSession())

    assert decision.route_kind == RouteKind.CLI_AGENT
    assert decision.fallback_reason == expected_tag
    assert decision.matched_signals == ("cli_agent_degraded", expected_tag)

    assert len(reported) == 1
    assert reported[0]["context"] == "interactive_shell.routing.handle_message_with_agent"
    payload = reported[0]["extra"]
    assert isinstance(payload, dict)
    assert payload["degrade_reason_tag"] == expected_tag
    assert payload["degrade_exception_class"] == type(exc).__name__
    assert payload["route_kind"] == RouteKind.CLI_AGENT.value


def test_map_actions_with_unhandled_raises_parse_error_on_clause_split_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_message: str) -> list[object]:
        raise ValueError("bad split")

    monkeypatch.setattr(_mapper, "split_prompt_clauses", _boom)

    with pytest.raises(ParseError):
        _mapper.map_actions_with_unhandled("show integrations")


def test_map_actions_with_unhandled_raises_policy_error_on_clause_mapping_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("bad policy path")

    monkeypatch.setattr(_mapper, "map_clause_actions", _boom)

    with pytest.raises(PolicyError):
        _mapper.map_actions_with_unhandled("show integrations")


def test_map_actions_with_unhandled_raises_planner_unavailable_on_finalize_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_text: str) -> object:
        raise RuntimeError("finalize failed")

    monkeypatch.setattr(_mapper, "extract_quoted_investigation_request_text", _boom)

    with pytest.raises(PlannerUnavailable):
        _mapper.map_actions_with_unhandled("please do this")
