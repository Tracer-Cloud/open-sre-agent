"""Floor applied when a metric gather never ran a live query.

Live tools can be connected and still fail to form a count query (unknown
event, schema-only probes). The answer must still include a labeled draft
query block (vendor-registered dialect, else a generic fence) and one
``/integrations setup …`` line, then stop — never invent a number, never burn
extra ``/goal`` turns.

Product cohort / signup-retention SessionGoals
(:mod:`core.agent_harness.turns.cohort_identity`) that leave identity open
after live probes also get a vendor cohort draft when one is registered.
Setup slash is omitted when the preferred analytics source is already
connected.

This policy applies for every preferred metric source — not a single vendor.
Only the draft *text* and optional observation parsers are integration-owned
via :mod:`infrastructure.harness_ports`.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from typing import Any

from core.agent_harness.turns.cohort_identity import (
    goal_needs_cohort_identity,
    reply_reports_cohort_unverified,
)
from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.evidence_need import EvidenceNeed, SetupCommandForSource
from core.agent_harness.turns.gather_discovery_budget import is_live_metric_query_call
from core.agent_harness.turns.gather_observation import (
    GatheredEvidence,
    coerce_gathered_evidence,
)
from infrastructure.harness_ports import (
    metric_cohort_resolved_for,
    metric_query_draft_for,
)

METRIC_UNFORMED_HANDOFF = "evidence_tier:metric_unformed"

# Last-resort fence when no analytics vendor registered a draft. Prefer empty
# over inventing a vendor dialect in core — vendors must opt in for real drafts.
_GENERIC_METRIC_DRAFT = """```text
-- Draft metric query: confirm metric name, filters, and window in your
-- analytics tool. This is not a live reading.
```"""


def _parse_arguments(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _iter_observation_calls(observation: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for block in (observation or "").split("\n\n"):
        if not block.startswith("Tool: "):
            continue
        name = block.split("\n", 1)[0][len("Tool: ") :].strip()
        _, _, rest = block.partition("\nArguments: ")
        args_text, _, _ = rest.partition("\nResult: ")
        yield name, _parse_arguments(args_text)


def gather_formed_live_metric_query(
    evidence: GatheredEvidence | None,
    *,
    metric_source_ids: tuple[str, ...] = (),
) -> bool:
    """True when gather executed a live metric / query tool call.

    Discovery probes and other non-metric fetches (issue lookup, tweet search,
    alert-rule roster, …) must not suppress the draft-query floor.

    Fixture / native gather often labels the block with the analytics source
    id when a query already ran — including syntax errors. Those stay L1
    (honest answer, no setup CTA).
    """
    if evidence is None:
        return False
    sources = frozenset(s.strip().lower() for s in metric_source_ids if str(s).strip())
    for name, arguments in _iter_observation_calls(evidence.observation):
        if is_live_metric_query_call(name, arguments):
            return True
        if name.strip().lower() in sources:
            return True
    # tool_results carry names only — use empty args so bridge calls without a
    # parsed tool_name cannot false-positive as execute-sql.
    if evidence.tool_results and not (evidence.observation or "").strip():
        for name, _payload in evidence.tool_results:
            tool = str(name or "")
            if is_live_metric_query_call(tool, {}):
                return True
            if tool.strip().lower() in sources:
                return True
    return False


def _setup_service_id(need: EvidenceNeed) -> str | None:
    if need.missing:
        return need.missing[0]
    if need.preferred_sources:
        return need.preferred_sources[0]
    if need.connected:
        return need.connected[0]
    return None


def _source_ids(need: EvidenceNeed) -> tuple[str, ...]:
    return (*need.preferred_sources, *need.connected, *need.missing)


def _draft_for(need: EvidenceNeed, *, cohort_goal: bool) -> str:
    draft = metric_query_draft_for(_source_ids(need), cohort_goal=cohort_goal)
    return draft if draft is not None else _GENERIC_METRIC_DRAFT


def _cohort_identity_resolved(
    need: EvidenceNeed,
    evidence: GatheredEvidence | None,
    reply: str,
) -> bool:
    """True when a vendor resolver says the cohort is live, else reply-only."""
    resolved = metric_cohort_resolved_for(_source_ids(need), evidence, reply)
    if resolved is not None:
        return resolved
    # No vendor resolver: an unverified reply keeps the floor; otherwise trust
    # a formed live query's answer.
    return not reply_reports_cohort_unverified(reply)


def _session_goal_condition(session: Any | None) -> str:
    """Read SessionGoal.condition without importing the session_goal package.

    Importing ``SessionGoal`` here would load ``session_goal/__init__`` →
    ``run_until`` → ``evaluate`` → this module and create a cycle.
    """
    if session is None:
        return ""
    goal = getattr(session, "session_goal", None)
    condition = getattr(goal, "condition", None)
    return condition if isinstance(condition, str) else ""


def _setup_line(
    need: EvidenceNeed,
    *,
    body: str,
    setup_command_for: SetupCommandForSource,
) -> str | None:
    """The connect command to append as its own line, or ``None`` for no line.

    ``None`` when there is no source to name, the surface renders no command,
    or the reply already carries one — a second line reads as a second
    instruction.
    """
    service_id = _setup_service_id(need)
    if service_id is None:
        return None
    command = setup_command_for(service_id)
    if not command or command in body:
        return None
    # The command minus the service id is the surface's connect verb, so a line
    # naming a different source is still a setup line. Taken from the rendered
    # command rather than a literal: core must not know the surface's syntax.
    verb = command.replace(service_id, "").strip(" `")
    if verb and verb in body:
        return None
    return command if command.startswith("`") else f"`{command}`"


def apply_unformed_metric_floor(
    response_text: str,
    need: EvidenceNeed,
    *,
    observation: str | GatheredEvidence | None,
    setup_command_for: SetupCommandForSource,
    session: Any | None = None,
    goal_condition: str | None = None,
) -> str:
    """Append a draft query (+ setup when needed) for unformed metric answers.

    No-op for non-metric turns. For ordinary metrics, no-op when a live query
    already ran. When the SessionGoal needs cohort identity and that identity
    is still open, still append a draft fence — even after candidate probes.
    Setup slash is skipped when the preferred source is already connected.
    """
    if need.kind is not EvidenceKind.METRIC_READ:
        return response_text
    evidence = coerce_gathered_evidence(observation)
    condition = goal_condition if goal_condition is not None else _session_goal_condition(session)
    cohort_goal = goal_needs_cohort_identity(condition)
    formed = gather_formed_live_metric_query(
        evidence,
        metric_source_ids=(*need.preferred_sources, *need.connected),
    )
    # Live query is enough unless this SessionGoal still needs cohort identity.
    if formed and (not cohort_goal or _cohort_identity_resolved(need, evidence, response_text)):
        return response_text

    body = (response_text or "").rstrip()
    parts: list[str] = [body] if body else []
    if "```" not in body:
        parts.append(_draft_for(need, cohort_goal=cohort_goal))
    # No live query: still append setup even when connected. Cohort floor after
    # live probes on an already-connected source: draft only — a reconnect CTA
    # is the wrong next step.
    if bool(need.missing) or not formed:
        setup_line = _setup_line(need, body=body, setup_command_for=setup_command_for)
        if setup_line is not None:
            parts.append(setup_line)
    return "\n\n".join(parts)


__all__ = [
    "METRIC_UNFORMED_HANDOFF",
    "apply_unformed_metric_floor",
    "gather_formed_live_metric_query",
]
