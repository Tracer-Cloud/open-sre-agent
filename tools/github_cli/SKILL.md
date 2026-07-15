---
name: github-cli
description: >
  Default GitHub skill for the action agent. Use github_cli for any GitHub
  request — create/list/view issues and PRs, assign, labels, repos, releases,
  checks, github.com/owner/repo URLs, or gh api. Prefer over shell_run/!gh.
  Never assistant_handoff these to gather — github_cli is action-only.
tools:
  - github_cli
---

# github-cli

Authenticated `gh` for OpenSRE. Reads and writes — no approval gate. Prefer over
`shell_run` / `!gh`. Pass `args` after `gh`; optional `repo` as `owner/name` → `-R`.

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

Use `summary` when present. Reply short and chat-like (Conversational chat tone).

- **Simple** (create/close/comment/merge, single URL/`#n`): plain prose, one
  sentence — no markdown needed.
- **Structured reads** (lists, status, checks): light chat markdown — short
  lead-in + bullets (`* #42 — title`). Prefer bullets over tables/headers.
  Truncate ("…and N more").
 - **Mutate extras:** at most 2–4 useful bullets. No GraphQL/JSON dumps
  (`mergeStateStatus`, `argv`, check matrices). No "I found:" on confirms.
  Skip "Want me to:" unless a concrete next step helps.
- **Failure:** one sentence from `error` / `error_type` — say it failed to run;
  do not invent success.

## Prefer dedicated tools when they clearly fit

Slack propose/execute mutations; workflow digests first. Investigation
code/commit search stays on dedicated tools (not github_cli). Multi-source
RCA that names github issues alongside Sentry/PostHog → investigation_start,
not github_cli.

## Limitations

`gh` on PATH; OpenSRE token auth. Projects v2 may 403. Token scopes limit writes.
Action-only — not available in gather/investigation.
