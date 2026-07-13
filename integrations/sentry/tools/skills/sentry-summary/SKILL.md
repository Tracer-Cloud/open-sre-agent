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

Up to 100 issues. Empty → widen to `7d` before reporting none. Results
include `digest` (clusters, top_issues, priority) — present that directly.

## 2. Classify

Cluster via `title`, `culprit`, `metadata`. **Default buckets** (OpenSRE
baseline; override when user supplies their own):

- Auth / API key errors
- Windows / OS install failures
- Backend timeouts or connection errors
- Frontend / UI crashes
- CI / pipeline failures
- Other / uncategorised

Label top issues: regression, auth (token/scope), or new failure (first in
window).

## 3. Rank

**Business impact** over raw counts. Weigh `userCount`, `count`, regression
flags, product context (onboarding drop-off > retry noise). Top 3–5 + one #1
priority.

## 4. Enrich (selective)

Never fetch all issues. Only top 3–5 and #1 priority: `get_sentry_issue_details`
+ `list_sentry_issue_events` (limit 10) when stack traces or regression proof
are needed.

## 5. Summarise

Slack-ready digest: total + window, cluster %, top 3–5 (title, cluster,
classification, events, users, seen range), priority call + why, next actions
(fix / monitor / investigation handoff with issue `id`).

## Traps

- `count` ≠ users — check `userCount`
- `stats_period` is relative — no absolute timestamps
- Detail APIs are expensive — enrich selectively
