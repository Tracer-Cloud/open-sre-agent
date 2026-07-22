---
name: github-cli
description: >
  GitHub-only product ops via github_cli (issues/PRs/repos/gh api). Prefer over
  shell_run/!gh. Never hand off to gather. Not for multi-source crash/RCA that
  names github issues with Sentry/PostHog — use investigation_start.
tools:
  - github_cli
---

# github-cli

Authenticated `gh` (read/write, no approval). Pass `args` after `gh`; optional
`repo` as `owner/name` → `-R`. Blocked: `auth`, `extension`, `workflow`, `run`,
`secret`, `codespace`, `ssh-key`, `gpg-key`, `config`.

## Do NOT use for multi-source RCA

Diagnosing a crash/failure/outage and naming GitHub among other sources
(sentry + github issues + posthog) → `investigation_start`, never `github_cli`.

## Capabilities

| Intent | Example `args` |
| --- | --- |
| Create issue | `["issue", "create", "--title", "…", "--body", "…"]` |
| List / view issues | `["issue", "list"]` / `["issue", "view", "42"]` |
| Close / comment / edit | `["issue", "close", "42"]` / `["issue", "comment", "42", "--body", "…"]` |
| List / view / merge PRs | `["pr", "list"]` / `["pr", "view", "45"]` / `["pr", "merge", "45"]` |
| Repos / search | `["repo", "list"]` / `["search", "issues", "crash"]` |
| Arbitrary API | `["api", "repos/OWNER/REPO/issues"]` |

## After github_cli returns

Use `summary`. Simple confirms: plain prose. Structured reads: light chat
markdown — short lead-in + bullets. Mutate extras: ≤4 bullets. No GraphQL/JSON
dumps. Failures: one sentence from `error` / `error_type` — say it failed to run.

## Prefer dedicated tools when they clearly fit

Slack propose/execute; workflow digests; investigation code/commit search.
Multi-source RCA (sentry + github issues + posthog) → investigation_start.

## Limitations

`gh` on PATH; OpenSRE token auth. Action-only — not in gather/investigation.
