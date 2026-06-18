from __future__ import annotations

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
    ParseError,
    PlannerUnavailable,
    PolicyError,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands import (
    deterministic_action_mapper as _mapper,
)


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
