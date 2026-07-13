---
name: sentry-summary
description: Summarise and cluster Sentry issues by theme, frequency, and business impact. Use for Sentry overviews, morning digests, reliability questions, or what to focus on.
tools:
  - search_sentry_issues
  - get_sentry_issue_details
  - list_sentry_issue_events
---

# Sentry Summary

Sentry-only (#3 on-demand, #10 morning digest). Multi-source prompts: finish
Sentry here, then suggest a multi-source investigation.

## 1. Fetch

`search_sentry_issues` with `query: "is:unresolved"` and window:

- **24h** — morning digest, overnight, today, scheduled #10
- **7d** — "this week", general overview
- Map user words (`last night` → `24h`, `this week` → `7d`)

Up to 100 issue groups (not events). Empty → widen to `7d`. Results include
`digest` with `structural_clusters`, `priority_candidates`, `top_issues`.

## 2. Classify

Use `digest.structural_clusters` (`key`, `label`, `sample_titles`). Map each
to a business theme in the answer. Never present bare project slugs without
explaining samples (e.g. CloudTrail creds, LLM quota, pipeline failures).

## 3. Rank

Use `digest.priority_candidates` and `business_impact_score` — not raw
`count` alone. Prefer userCount, operational blockers (credentials, quota,
pipeline stop), regressions. Penalize high events + zero users (retry noise).
State impact_reasons in the priority call.

## 4. Enrich (selective)

Only top 3–5 and #1 priority: `get_sentry_issue_details` +
`list_sentry_issue_events` (limit 10) when traces/regression proof needed.

## 5. Summarise

Slack-ready digest:

- **I found:** quote `digest.scope_summary` verbatim (total issue groups + window).
- Themed cluster breakdown: each cluster as `N issues (P%)` with `sample_short_ids`
  when present (e.g. `PYTHON-G4`, `TRACER-CLIENT-12`).
- Priority table: rank clusters; columns Priority | Cluster | Issues | Sample IDs |
  Why it matters (from `impact_reasons`).
- Top 3–5 issues and next actions (fix / monitor / investigation handoff).

## Traps

- `count` = events in the issue group; `issue_count` = issue groups returned
- `stats_period` is relative — no absolute timestamps
- Detail APIs are expensive — enrich selectively
