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

`search_sentry_issues` once with `query: "is:unresolved"`:

- **24h** — morning digest, overnight, today (#10)
- **7d** — "this week", general overview
- Map user words (`last night` → `24h`, `this week` → `7d`)

One API page, max 100 issue groups (not events). Use `digest.page_saturated`
(`100+` + `scope_note`) vs `page_complete` (exact count). When
`completeness` is `empty`, report no groups in the requested window — do not
widen `stats_period` or run a second search unless the user asks; label any
broader follow-up separately. Digest has `structural_clusters`,
`priority_candidates`, `top_issues`.

## 2. Classify

Use `digest.structural_clusters` (`label`, `sample_titles`, `sample_short_ids`).
Map to business themes — never bare project slugs without sample context.

## 3. Rank

Use `digest.priority_candidates` and `business_impact_score`, not raw `count`.
Prefer userCount, operational blockers, regressions; penalize high events +
zero users (retry noise). Cite `impact_reasons`.

## 4. Enrich (selective)

Top 3–5 / #1 only: `get_sentry_issue_details` + `list_sentry_issue_events`
(limit 10) when traces or regression proof needed.

## 5. Summarise

- **I found:** `digest.scope_summary`; add `scope_note` when capped or ambiguous.
- Clusters: `N issues (P%)` + `sample_short_ids` (`percent` = returned page).
- Priority table: Priority | Cluster | Issues | Sample IDs | Why (impact_reasons).

## Traps

- `count` = events per group; `issue_count` = groups returned
- `page_saturated` = first page only; `page_complete` = exact for window
- `completeness: empty` = zero groups in requested window; never silent widen
- `stats_period` is relative; detail APIs are expensive — enrich selectively
