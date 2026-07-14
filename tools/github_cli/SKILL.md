---
name: github-cli
description: >
  Default GitHub skill for chat. Use github_cli for any GitHub request —
  create/list/view issues and PRs, assign, labels, repos, releases, checks,
  github.com/owner/repo URLs, or gh api. Prefer over shell_run/!gh.
tools:
  - github_cli
---

# github-cli

Authenticated `gh` for OpenSRE. **Reads and writes** — no separate write tool,
no approval gate. Prefer over `shell_run` / `!gh`.

Pass `args` after `gh`. Optional `repo` as `owner/name` → `-R`. End with a short
human summary (URLs, titles, counts).

## Capabilities

| Intent | Example `args` |
| --- | --- |
| Create issue | `["issue", "create", "--title", "…", "--body", "…", "--assignee", "user"]` |
| List / view issues | `["issue", "list", "--limit", "20"]` / `["issue", "view", "42"]` |
| Close / comment / edit | `["issue", "close", "42"]` / `["issue", "comment", "42", "--body", "…"]` |
| List / view PRs | `["pr", "list"]` / `["pr", "view", "45"]` |
| PR checks / merge | `["pr", "checks", "45"]` / `["pr", "merge", "45"]` |
| Repos | `["repo", "list", "--limit", "30"]` / `["repo", "view"]` |
| Search | `["search", "issues", "crash"]` / `["search", "prs", "fix"]` |
| Releases / labels / runs | `["release", "list"]` / `["label", "list"]` / `["run", "list"]` |
| Arbitrary API | `["api", "repos/OWNER/REPO/issues"]` |

Add flags as needed (`--label`, `--json`, `--jq`). Use `["api", …]` when no
subcommand fits.

## Prefer dedicated tools when they clearly fit

- Slack propose/execute mutation tools
- Investigation search: `search_github_issues`
- Work-status / security digests: workflow tools first

## Limitations

`gh` on PATH; OpenSRE token auth. Projects v2 may 403. Token scopes limit writes.
