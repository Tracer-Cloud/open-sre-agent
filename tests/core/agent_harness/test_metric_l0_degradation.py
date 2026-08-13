"""Metric ask with preferred source missing: skip gather, CTA, no investigate offer.

Action agent must emit ``evidence_kind:metric_read``; harness does not scan user
prose for metric intent.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.session.pending_offer import (
    PendingIntegrationSetupOffer,
    first_pending_offer,
)
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.evidence_need import EvidenceKind
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from surfaces.interactive_shell.session import Session

_WINDOWS_USERS_ASK = "how many windows users did we have in the last 7 days"


def _action_metric_handoff() -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=True,
        handled=False,
        handoff_contents=(
            "evidence_kind:metric_read",
            "Look up the Windows user count in product analytics.",
        ),
    )


def _action_metric_handoff_typed() -> ToolCallingTurnResult:
    """Schema-field path: evidence_kind on AssistantHandoff, no content tag."""
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=True,
        handled=False,
        handoff_contents=("Look up the Windows user count in product analytics.",),
        assistant_handoffs=(
            AssistantHandoff(
                content="Look up the Windows user count in product analytics.",
                evidence_kind=EvidenceKind.METRIC_READ,
            ),
        ),
    )


def test_before_after_metric_ask_skips_gather_and_adds_cta_without_investigate_offer() -> None:
    session = Session()
    session.resolved_integrations_cache = {}
    gather_calls: list[str] = []
    answer_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return _action_metric_handoff()

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: list_posthog_tools\nResult: (discovery only — should not run)"

    def _answer(text: str, request: AnswerRequest, **_kwargs: Any) -> Any:
        answer_calls.append(text)
        return type(
            "Run",
            (),
            {
                "response_text": (
                    "Without live analytics I can outline a count query, "
                    "but I cannot return the real number yet."
                )
            },
        )()

    result = run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == [], "gather must not run when preferred source is missing"
    assert answer_calls == [_WINDOWS_USERS_ASK]
    text = (result.assistant_response_text or "").strip()
    assert "/integrations setup posthog_mcp" in text
    assert "```sql" in text
    assert "SELECT" in text
    # Investigate offer suppressed; setup offer armed so bare yes connects.
    offer = first_pending_offer(session)
    assert isinstance(offer, PendingIntegrationSetupOffer)
    assert offer.service_id == "posthog_mcp"
    assert session.pending_investigation_offer is None


def test_metric_ask_with_source_connected_still_gathers() -> None:
    session = Session()
    session.resolved_integrations_cache = {"posthog_mcp": {"configured": True}}
    gather_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return _action_metric_handoff()

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: execute-sql\nResult: windows_users=42"

    run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=lambda *_a, **_k: None,
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == [_WINDOWS_USERS_ASK]


def test_connected_source_auth_failure_gathers_then_appends_reconnect_cta() -> None:
    """L1 gather that hits config/auth failure must CTA without skipping gather."""
    session = Session()
    session.resolved_integrations_cache = {"posthog_mcp": {"url": "https://mcp.example"}}
    gather_calls: list[str] = []
    answer_handoffs: list[tuple[str, ...]] = []

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        # Typed tool_unavailable envelope (Python repr — how gather often
        # stringifies dict results). Must not rely on prose "401" alone.
        return (
            "Tool: posthog_mcp execute-sql\n"
            "Result: {'available': False, 'error': '401 Unauthorized — invalid api key'}"
        )

    def _answer(text: str, request: AnswerRequest, **_kwargs: Any) -> Any:
        answer_handoffs.append(tuple(request.handoff_contents or ()))
        return type(
            "Run",
            (),
            {
                "response_text": (
                    "PostHog returned 401 — credentials look wrong. "
                    "Draft HogQL once reconnect works."
                )
            },
        )()

    result = run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=lambda *_a, **_k: _action_metric_handoff(),
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == [_WINDOWS_USERS_ASK]
    text = (result.assistant_response_text or "").strip()
    assert "/integrations setup posthog_mcp" in text
    assert "credentials or configuration" in text
    assert any(
        any(tag.startswith("evidence_tier:L0_degraded:config:") for tag in tags)
        for tags in answer_handoffs
    )
    offer = first_pending_offer(session)
    assert isinstance(offer, PendingIntegrationSetupOffer)
    assert offer.service_id == "posthog_mcp"
    assert session.pending_investigation_offer is None


def test_connected_source_hogql_failure_gathers_without_cta() -> None:
    """Query/syntax failures stay L1 — honest answer, no reconnect CTA."""
    session = Session()
    session.resolved_integrations_cache = {"posthog_mcp": {"url": "https://mcp.example"}}
    gather_calls: list[str] = []

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: posthog_mcp\nResult: HogQL syntax error near WHERE"

    result = run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=lambda *_a, **_k: _action_metric_handoff(),
        gather=_gather,
        answer=lambda *_a, **_k: type(
            "Run",
            (),
            {"response_text": "The HogQL query failed; try a simpler property."},
        )(),
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == [_WINDOWS_USERS_ASK]
    text = (result.assistant_response_text or "").strip()
    assert "/integrations setup" not in text
    assert first_pending_offer(session) is None


def test_connected_source_without_a_live_query_still_drafts_hogql_and_setup() -> None:
    """Analytics connected, only schema/list probes — draft + setup, then stop."""
    session = Session()
    session.resolved_integrations_cache = {"posthog_mcp": {"configured": True}}
    answer_handoffs: list[tuple[str, ...]] = []

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        return (
            "Tool: list_posthog_tools\nArguments: {}\nResult: []\n\n"
            "Tool: call_posthog_tool\n"
            'Arguments: {"tool_name": "event-definitions"}\n'
            "Result: []"
        )

    def _answer(text: str, request: AnswerRequest, **_kwargs: Any) -> Any:
        answer_handoffs.append(tuple(request.handoff_contents or ()))
        return type(
            "Run",
            (),
            {"response_text": ("I could not identify a signup event, so I cannot return a count.")},
        )()

    result = run_turn(
        "How many Windows users signed up in the last 7 days?",
        session,
        execute_actions=lambda *_a, **_k: _action_metric_handoff(),
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(
            session, "How many Windows users signed up in the last 7 days?"
        ),
    )

    text = (result.assistant_response_text or "").strip()
    assert "```sql" in text
    assert "SELECT" in text
    assert "/integrations setup posthog_mcp" in text
    assert any("evidence_tier:metric_unformed" in tags for tags in answer_handoffs)
    assert session.pending_investigation_offer is None


def test_without_evidence_kind_handoff_does_not_skip_gather() -> None:
    """User prose alone must not trigger L0 skip — action must tag the kind."""
    session = Session()
    session.resolved_integrations_cache = {}
    gather_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("Look up the Windows user count in product analytics.",),
        )

    run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=_execute,
        gather=lambda text, *_a, **_k: gather_calls.append(text) or "evidence",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "ok"})(),
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == [_WINDOWS_USERS_ASK]


def test_typed_evidence_kind_schema_field_skips_gather_like_content_tag() -> None:
    """Planner fills evidence_kind on tool JSON — must match tag-path L0 behavior."""
    session = Session()
    session.resolved_integrations_cache = {}
    gather_calls: list[str] = []

    run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=lambda *_a, **_k: _action_metric_handoff_typed(),
        gather=lambda text, *_a, **_k: gather_calls.append(text) or "should-not-run",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Connect PostHog."})(),
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert gather_calls == []
    offer = first_pending_offer(session)
    assert isinstance(offer, PendingIntegrationSetupOffer)
    assert offer.service_id == "posthog_mcp"


def test_upgrade_cta_reoffers_when_user_asks_again() -> None:
    """A repeat metric ask after moving on clears the stale pending offer and re-CTAs.

    Forever session-wide dedupe blocked a second ask after the user declined /
    changed topic. Deduping while a pending setup offer is still armed is covered
    in ``test_upgrade_cta_reoffer``.
    """
    session = Session()
    session.resolved_integrations_cache = {}

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return _action_metric_handoff()

    def _answer(*_args: object, **_kwargs: Any) -> Any:
        return type("Run", (), {"response_text": "Draft query outline."})()

    first = run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "should-not-run",
        answer=_answer,
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )
    second = run_turn(
        _WINDOWS_USERS_ASK,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "should-not-run",
        answer=_answer,
        accounting=DefaultTurnAccounting(session, _WINDOWS_USERS_ASK),
    )

    assert first.assistant_response_text.count("/integrations setup posthog_mcp") == 1
    assert "/integrations setup posthog_mcp" in (second.assistant_response_text or "")
