"""Cursor-like floor when a metric gather never ran a live query.

Parity S2: live tools can be connected and still fail to form a count query
(unknown event, schema-only probes). The answer must still include a labeled
draft HogQL or PromQL block and one ``/integrations setup …`` line, then stop —
never invent a number, never burn extra ``/goal`` turns.

Parity S9: signup / retention goals can run live probes and still leave the
signup event unverified. Those replies also get a draft HogQL fence. Setup
slash is omitted when the preferred analytics source is already connected.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from typing import Any

from core.agent_harness.turns.evidence_kind import EvidenceKind
from core.agent_harness.turns.evidence_need import EvidenceNeed, SetupCommandForSource
from core.agent_harness.turns.gather_discovery_budget import is_live_metric_query_call
from core.agent_harness.turns.gather_observation import (
    GatheredEvidence,
    coerce_gathered_evidence,
)
from core.agent_harness.turns.signup_identity import (
    SIGNUP_EVENT_UNVERIFIED_MARK,
    goal_condition_asks_signup_or_retention,
    reply_reports_signup_unverified,
)

METRIC_UNFORMED_HANDOFF = "evidence_tier:metric_unformed"

_DRAFT_HOGQL = """```sql
-- Draft HogQL: confirm event name and property filters, then run in PostHog.
-- This is not a live count.
SELECT uniq(person_id)
FROM events
WHERE timestamp >= now() - INTERVAL 7 DAY
```"""

_DRAFT_HOGQL_SIGNUP = """```sql
-- Draft HogQL: replace <signup_event> with your project's signup event
-- (not user_signed_in / login). This is not a live retention percentage.
SELECT
  count() AS eligible,
  countIf(dateDiff('day', signup_at, activity_at) = 7) AS retained_d7
FROM (
  SELECT
    person_id,
    min(timestamp) AS signup_at
  FROM events
  WHERE event = '<signup_event>'
    AND properties.$os = 'Windows'
    AND timestamp >= now() - INTERVAL 30 DAY
  GROUP BY person_id
) AS signups
LEFT JOIN (
  SELECT person_id, timestamp AS activity_at
  FROM events
) AS activity USING (person_id)
```"""

_DRAFT_PROMQL = """```promql
-- Draft PromQL: confirm metric name and window, then run in Grafana.
-- This is not a live reading.
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1h])) by (le))
```"""

# Login / identify stand-ins — never treat these as verified signup events.
_LOGIN_STANDIN_EVENTS = frozenset(
    {
        "user_signed_in",
        "signed_in",
        "login",
        "user_login",
        "log_in",
        "$identify",
    }
)

_EVENT_EQ_RE = re.compile(
    r"""event\s*=\s*['\"]([^'\"]+)['\"]""",
    re.IGNORECASE,
)


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


def _sql_events_referenced(observation: str) -> frozenset[str]:
    return frozenset(match.group(1).strip() for match in _EVENT_EQ_RE.finditer(observation or ""))


def _signup_identity_resolved(evidence: GatheredEvidence | None, reply: str) -> bool:
    """True when a non-login signup event was queried and the reply is a live %.

    Empty cohorts / unavailable / login stand-ins stay unresolved so the draft
    fence still lands.
    """
    if reply_reports_signup_unverified(reply):
        return False
    if evidence is None:
        return False
    events = _sql_events_referenced(evidence.observation)
    if not events:
        return False
    if events <= _LOGIN_STANDIN_EVENTS:
        return False
    lower = (reply or "").casefold()
    return "unavailable" not in lower and "unverified" not in lower


def _setup_service_id(need: EvidenceNeed) -> str | None:
    if need.missing:
        return need.missing[0]
    if need.preferred_sources:
        return need.preferred_sources[0]
    if need.connected:
        return need.connected[0]
    return None


def _draft_for(need: EvidenceNeed, *, signup_goal: bool) -> str:
    sources = " ".join((*need.preferred_sources, *need.connected, *need.missing)).lower()
    if "grafana" in sources:
        return _DRAFT_PROMQL
    if signup_goal:
        return _DRAFT_HOGQL_SIGNUP
    return _DRAFT_HOGQL


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
    already ran. For signup/retention SessionGoals, still append a draft HogQL
    fence when signup identity is unresolved — even after candidate probes.
    Setup slash is skipped when the preferred source is already connected.
    """
    if need.kind is not EvidenceKind.METRIC_READ:
        return response_text
    evidence = coerce_gathered_evidence(observation)
    condition = goal_condition if goal_condition is not None else _session_goal_condition(session)
    signup_goal = goal_condition_asks_signup_or_retention(condition)
    formed = gather_formed_live_metric_query(
        evidence,
        metric_source_ids=(*need.preferred_sources, *need.connected),
    )
    if formed and not (signup_goal and not _signup_identity_resolved(evidence, response_text)):
        return response_text
    if signup_goal and _signup_identity_resolved(evidence, response_text):
        return response_text

    parts: list[str] = []
    body = (response_text or "").rstrip()
    if body:
        parts.append(body)
    if "```" not in body:
        parts.append(_draft_for(need, signup_goal=signup_goal))
    # Classic S2 (no live query): still append setup even when connected.
    # Signup floor after live probes on an already-connected source: draft
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
    "SIGNUP_EVENT_UNVERIFIED_MARK",
    "apply_unformed_metric_floor",
    "gather_formed_live_metric_query",
    "goal_condition_asks_signup_or_retention",
    "reply_reports_signup_unverified",
]
