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

Authenticated GitHub CLI (`gh`) for OpenSRE. **One tool for reads and writes**
— no separate write tool, no approval gate.

## When to use

Anything plausible about GitHub: create/list/view issues or PRs; assign; labels;
repo list; releases; checks; questions naming `github.com/owner/repo`.

## Important Functions

Use `github_cli` — never raw `git`/`gh` in `shell_run` or `!gh`.

Pass `args` as the list **after** `gh`. Optional `repo` as `owner/name` → `-R`.

### Examples

- Create: `["issue", "create", "--title", "…", "--body", "…", "--assignee", "joe"]`
- List: `["issue", "list", "--limit", "20"]`
- Repos: `["repo", "list", "--limit", "30"]`
- View PR: `["pr", "view", "45"]`

Always end with a short human summary (issue URL, titles, counts).

## Prefer dedicated tools only when they clearly fit

- Slack propose/execute mutation tools
- Investigation keyword search: `search_github_issues`
- Work-status / security digests: workflow tools first

## Known Limitations

- `gh` must be on PATH; auth from OpenSRE GitHub token env.
- Projects v2 may 403.
