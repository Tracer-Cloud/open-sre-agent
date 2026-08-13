"""PostHog MCP owns HogQL draft fences and signup-event identity checks.

Core's metric floor decides *when* to append a draft and when a SessionGoal
needs cohort identity. This package supplies the PostHog dialect (HogQL) and
how to tell a real signup event from a login stand-in in gather observations.
"""

from __future__ import annotations

import re
from typing import Any

from core.agent_harness.turns.cohort_identity import reply_reports_cohort_unverified
from core.agent_harness.turns.gather_observation import (
    coerce_gathered_evidence,
)
from platform.harness_ports import (
    register_discovery_targets,
    register_metric_cohort_resolver,
    register_metric_query_draft,
    register_metric_query_tools,
)

# PostHog MCP bridge targets: which run a query, which only read schema.
_METRIC_QUERY_TOOLS = ("execute-sql", "query-trends", "query-run")
_DISCOVERY_TARGETS = (
    "docs-search",
    "event-definitions",
    "property-definitions",
    "property-values",
)

_DRAFT_HOGQL_COUNT = """```sql
-- Draft HogQL: confirm event name and property filters, then run in PostHog.
-- This is not a live count.
SELECT uniq(person_id)
FROM events
WHERE timestamp >= now() - INTERVAL 7 DAY
```"""

_DRAFT_HOGQL_COHORT = """```sql
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

# Substrings that mark an event as the one a person signs up with. Login and
# identify events carry none of them, so they can never stand in for signup.
_SIGNUP_EVENT_MARKERS = (
    "signup",
    "sign_up",
    "signed_up",
    "registered",
    "registration",
    "account_created",
    "created_account",
)

_EVENT_EQ_RE = re.compile(
    r"""event\s*=\s*['\"]([^'\"]+)['\"]""",
    re.IGNORECASE,
)


def _sql_events_referenced(observation: str) -> frozenset[str]:
    return frozenset(match.group(1).strip() for match in _EVENT_EQ_RE.finditer(observation or ""))


def _names_a_signup_event(event: str) -> bool:
    lowered = event.casefold()
    return any(marker in lowered for marker in _SIGNUP_EVENT_MARKERS)


def posthog_signup_cohort_resolved(evidence: Any, reply: str) -> bool:
    """True when gather queried a named signup event and the reply is a live %."""
    if reply_reports_cohort_unverified(reply):
        return False
    gathered = coerce_gathered_evidence(evidence)
    if gathered is None:
        return False
    events = _sql_events_referenced(gathered.observation)
    if not any(_names_a_signup_event(event) for event in events):
        return False
    lower = (reply or "").casefold()
    return "unavailable" not in lower and "unverified" not in lower


def register_posthog_mcp_metric_drafts() -> None:
    """Opt PostHog MCP into the unformed-metric draft floor."""
    register_metric_query_draft(
        "posthog_mcp",
        count_draft=_DRAFT_HOGQL_COUNT,
        cohort_draft=_DRAFT_HOGQL_COHORT,
        priority=10,
    )
    register_metric_cohort_resolver("posthog_mcp", posthog_signup_cohort_resolved)
    register_metric_query_tools("posthog_mcp", _METRIC_QUERY_TOOLS)
    register_discovery_targets("posthog_mcp", _DISCOVERY_TARGETS)


__all__ = [
    "posthog_signup_cohort_resolved",
    "register_posthog_mcp_metric_drafts",
]
