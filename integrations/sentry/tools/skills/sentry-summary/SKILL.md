---
name: sentry-summary
description: Summarise and cluster Sentry issues by theme, frequency, and business impact. Use when the user asks about Sentry errors, product reliability, what to focus on, or wants a monitoring report.
tools:
  - search_sentry_issues
  - get_sentry_issue_details
  - list_sentry_issue_events
---

# Sentry Summary

Use this skill when the user asks for a Sentry overview, SRE monitoring report,
product reliability summary, or wants to know what issues to focus on.

## Step-by-step approach

1. **Fetch the issue list.** Call `search_sentry_issues` with no query and the
   default `stats_period` (`24h`). This returns up to 100 issues — the full
   recent page. If the user specified a time window (e.g. "last week"), pass
   that as `stats_period` (e.g. `7d`, `14d`, `30d`).

2. **Cluster by theme.** Group issues by shared root cause or symptom. Typical
   clusters for OpenSRE:
   - Auth / API key errors
   - Windows / OS-specific install failures
   - Backend timeouts or connection errors
   - Frontend / UI crashes
   - CI / pipeline failures
   - Other / uncategorised

   Use the `title`, `culprit`, and `metadata` fields from each issue object to
   assign clusters. A single regex pass on the title is enough for most groupings.

3. **Rank by business impact.** Within each cluster, sort by `count` (total
   events) descending. Surface the top issue per cluster. If `userCount` is
   available, factor it in — a low-count issue affecting many users ranks higher
   than a high-count issue affecting one.

4. **Drill into the top issue only.** For the single highest-priority issue,
   call `get_sentry_issue_details` (pass the issue `id` from step 1). This
   gives `culprit`, `level`, `firstSeen`, `lastSeen`, and regression flags.
   Then call `list_sentry_issue_events` to pull the 10 most recent stack traces.

5. **State the priority call.** Pick the issue most likely linked to user
   drop-off or production breakage. State why it is the top priority. Hand off
   its `id` to the investigation tool if a full RCA is needed.

## Critical traps

- **Do not fetch details for every issue.** `get_sentry_issue_details` is
  expensive. Only drill into the single highest-priority issue unless the user
  explicitly asks for more.
- **Empty results ≠ no issues.** If `search_sentry_issues` returns an empty
  list, widen the `stats_period` to `7d` before reporting nothing found.
- **`count` is events, not users.** A high `count` on an automated retry loop
  looks severe but may affect zero real users. Always cross-reference
  `userCount` when present.
- **`stats_period` is relative to now.** If the user says "last night", use
  `24h`. If they say "this week", use `7d`. Do not pass absolute timestamps.

## Output shape

Return a Slack-ready summary with:

- **Total issues** in the window and the `stats_period` used
- **Cluster breakdown** — issue count and % per cluster
- **Top 3–5 issues** — title, cluster, event count, user count, first/last seen
- **Priority call** — single sentence: which issue to fix first and why
- **Next step** — suggest passing the top issue `id` to the investigation tool
  if RCA is needed

### Example output

```
🔴 47 Sentry issues in the last 24h

By theme:
  • Auth / API key errors    — 29 issues (62%)
  • Windows install failures — 10 issues (21%)
  • Backend timeouts         —  4 issues  (9%)
  • Other                    —  4 issues  (8%)

Top issues:
  1. APIKeyMissingError in auth/validate — 1,240 events, 83 users  [auth]
  2. WindowsInstallFailed at setup.exe  —   312 events, 47 users  [windows]
  3. ConnectionTimeout in backend/ingest —   98 events,  6 users  [timeout]

Priority: #2 WindowsInstallFailed — highest unique user impact (47 users),
likely linked to onboarding drop-off. Pass issue id 123456 to the
investigation tool for full RCA.
```
