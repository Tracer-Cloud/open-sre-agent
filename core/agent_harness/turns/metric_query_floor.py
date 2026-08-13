"""Cursor-like floor when a metric gather never ran a live query.

Parity S2: live tools can be connected and still fail to form a count query
(unknown event, schema-only probes). The answer must still include a labeled
draft HogQL or PromQL block and one ``/integrations setup …`` line, then stop —
never invent a number, never burn extra ``/goal`` turns.
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

METRIC_UNFORMED_HANDOFF = "evidence_tier:metric_unformed"

_DRAFT_HOGQL = """```sql
-- Draft HogQL: confirm event name and property filters, then run in PostHog.
-- This is not a live count.
SELECT uniq(person_id)
FROM events
WHERE timestamp >= now() - INTERVAL 7 DAY
```"""

_DRAFT_PROMQL = """```promql
-- Draft PromQL: confirm metric name and window, then run in Grafana.
-- This is not a live reading.
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1h])) by (le))
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
    """True when gather executed a live metric/SQL/PromQL query.

    Discovery probes and other non-metric fetches (issue lookup, tweet search,
    alert-rule roster, …) must not suppress the draft-query floor.

    Fixture / native gather often labels the block with the analytics source
    id (``Tool: posthog_mcp``) when a HogQL query already ran — including
    syntax errors. Those stay L1 (honest answer, no setup CTA).
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


def _draft_for(need: EvidenceNeed) -> str:
    sources = " ".join((*need.preferred_sources, *need.connected, *need.missing)).lower()
    if "grafana" in sources:
        return _DRAFT_PROMQL
    return _DRAFT_HOGQL


def apply_unformed_metric_floor(
    response_text: str,
    need: EvidenceNeed,
    *,
    observation: str | GatheredEvidence | None,
    setup_command_for: SetupCommandForSource,
) -> str:
    """Append a draft query + one setup slash when no live metric query ran.

    No-op for non-metric turns and for gathers that already executed a query.
    Does not duplicate a fence or setup command already present in the reply.
    """
    if need.kind is not EvidenceKind.METRIC_READ:
        return response_text
    evidence = coerce_gathered_evidence(observation)
    if gather_formed_live_metric_query(
        evidence,
        metric_source_ids=(*need.preferred_sources, *need.connected),
    ):
        return response_text

    parts: list[str] = []
    body = (response_text or "").rstrip()
    if body:
        parts.append(body)
    if "```" not in body:
        parts.append(_draft_for(need))
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
