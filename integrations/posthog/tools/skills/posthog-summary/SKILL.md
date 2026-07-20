---
name: posthog-summary
description: Summarise PostHog product analytics into a per-metric report (like a Viktor report). Use for PostHog usage overviews, per-metric summaries, product-analytics digests, or "what happened this week" reporting.
tools:
  - list_posthog_tools
  - call_posthog_tool
---

# PostHog Summary

PostHog product-analytics reporting. Produce a per-metric summary report over a
window. Single source (PostHog); finish here, then optionally suggest a
multi-source follow-up.

## 1. Discover

`list_posthog_tools` once with `name_filter: "insights dashboard sql events"` to
find the query surface. Then call `read-data-schema` (or the equivalent schema
tool) BEFORE any aggregation query and only reference events and properties it
confirms exist. Do not reference a property (e.g. `properties.$mcp_error`)
without seeing it in the schema first — HogQL rejects queries against unknown
properties. To pull metric data, use `call_posthog_tool` with
`tool_name: "execute-sql"`; use insight/dashboard tools when the user names a
specific dashboard.

## 2. Fetch metrics

Map user words to windows:

- **24h** — "today", "overnight", morning report
- **7d** — "this week", default weekly report
- **30d** — "this month", monthly rollup

Run **one small HogQL query per metric** — do NOT combine multiple metrics into a
single aggregated statement, and never cross-join events against synthetic
"window" rows. That combined shape is the most common cause of PostHog's
`unknown error running this query`. Compute the two windows with an explicit
conditional aggregate over a single time filter, e.g.:

```sql
SELECT
  countIf(timestamp >= now() - INTERVAL 7 DAY) AS current,
  countIf(timestamp >= now() - INTERVAL 14 DAY
          AND timestamp < now() - INTERVAL 7 DAY) AS previous
FROM events
WHERE event = '$pageview'
```

Swap `event = '...'` (or `count(DISTINCT person_id)` for active users) per
metric. If a query still fails, retry that single metric once with a simpler
`SELECT count() ... WHERE timestamp >= now() - INTERVAL 7 DAY`, then report the
metric as failed rather than aborting the whole report.

Default metric set when the user does not name specific metrics (restrict to
events confirmed present in the schema):

- Active users (`count(DISTINCT person_id)`)
- Key events (`$pageview`, sessions, or the project's top custom events)
- New signups / new users if an identifying event exists
- Notable custom events surfaced by the schema

## 3. Compute per-metric deltas

For each metric: current value, previous value, absolute change, percent change,
and direction (up / down / flat). Flag a metric as notable when the percent
change exceeds ~15% in either direction, or when a value hits zero unexpectedly.

## 4. Summarise (per-metric report)

Open with **I found:** a one-line scope summary (window + project + metric
count). Then a per-metric table:

Metric | Current | Previous | Change | % | Trend

- Trend: use up / down / flat words, never rely on colour alone.
- Call out the 1-3 most notable movers with a short "why it matters" line.
- When a window returns no data, say so explicitly for that metric — never
  silently widen the window or invent numbers.

## Traps

- HogQL time ranges are relative; state the absolute window in the report.
- Distinguish "metric returned zero" (real) from "query failed" (report the
  failure, do not present it as zero).
- The MCP server exposes 240+ tools; narrow with `name_filter` before calling —
  never dump the full listing.
- One bounded query per metric group; event-level scans are expensive.
- `unknown error running this query` almost always means the statement was too
  complex (multiple metrics combined, cross-joins, or an unverified property).
  Recover by splitting into one simple per-metric query, not by giving up.
