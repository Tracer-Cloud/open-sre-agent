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



- Slack propose/execute mutation tools
- Investigation search: `search_github_issues`
- Work-status / security digests: workflow tools first

## Limitations

`gh` on PATH; OpenSRE token auth. Projects v2 may 403. Token scopes limit writes.
