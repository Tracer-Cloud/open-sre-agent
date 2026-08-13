"""Floor applied when a metric gather never ran a live query.

Live tools can be connected and still fail to form a count query (unknown
event, schema-only probes). The answer must still include a labeled draft
query block (vendor-registered dialect) and one ``/integrations setup …``
line, then stop — never invent a number, never burn extra ``/goal`` turns.

When a registered vendor says the SessionGoal needs cohort identity and that
identity is still open after live probes, those replies also get the vendor's
cohort draft fence. Setup slash is omitted when the preferred analytics source
is already connected.

Vendor dialect, goal matchers, and unverified-reply detectors live in
integrations and opt in via :mod:`platform.harness_ports` — this module stays
source-agnostic.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from typing import Any

from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.evidence_need import EvidenceNeed, SetupCommandForSource
from core.agent_harness.turns.gather_discovery_budget import is_live_metric_query_call
from core.agent_harness.turns.gather_observation import (
    GatheredEvidence,
    coerce_gathered_evidence,
)
from platform.harness_ports import (
    metric_cohort_resolved_for,
    metric_goal_needs_cohort_identity,
    metric_query_draft_for,
    metric_reply_reports_cohort_unverified,
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
    return not metric_reply_reports_cohort_unverified(reply)


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
    already ran. When a registered vendor marks the SessionGoal as needing
    cohort identity and that identity is still open, still append a vendor
    draft fence — even after candidate probes. Setup slash is skipped when the
    preferred source is already connected.
    """
    if need.kind is not EvidenceKind.METRIC_READ:
        return response_text
    evidence = coerce_gathered_evidence(observation)
    condition = goal_condition if goal_condition is not None else _session_goal_condition(session)
    cohort_goal = metric_goal_needs_cohort_identity(condition)
    formed = gather_formed_live_metric_query(
        evidence,
        metric_source_ids=(*need.preferred_sources, *need.connected),
    )
    if formed:
        if not cohort_goal:
            return response_text
        if _cohort_identity_resolved(need, evidence, response_text):
            return response_text

    parts: list[str] = []
    body = (response_text or "").rstrip()
    if body:
        parts.append(body)
    if "```" not in body:
        parts.append(_draft_for(need, cohort_goal=cohort_goal))
    # No live query: still append setup even when connected.
    # Cohort floor after live probes on an already-connected source: draft
    # only — a reconnect CTA is the wrong next step.
    append_setup = bool(need.missing) or not formed
    if append_setup:
        service_id = _setup_service_id(need)
        if service_id is not None:
            command = setup_command_for(service_id)
            if command and command not in body and "/integrations setup" not in body:
                parts.append(f"`{command}`" if not command.startswith("`") else command)
    return "\n\n".join(parts)


__all__ = [
    "METRIC_UNFORMED_HANDOFF",
    "apply_unformed_metric_floor",
    "gather_formed_live_metric_query",
]
