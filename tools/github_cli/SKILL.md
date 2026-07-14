---
name: github-cli
description: >
  Default GitHub skill for chat. Use for any GitHub request —
  issues, PRs, repos, releases, labels, checks, workflows, repo access,
  github.com/owner/repo URLs, or flexible gh api — via github_cli /
  github_cli_write. Prefer these over shell_run/raw gh. Only defer to
  Slack propose/execute or search_github_issues when those paths clearly fit.
tools:
  - github_cli
  - github_cli_write
---

# github-cli

Authenticated GitHub CLI (`gh`) for OpenSRE. **Default path for GitHub work
in chat.** Prefer these tools over `shell_run` with raw `gh` or guessing.

## When to use

Anything plausible about GitHub: create/list/view issues or PRs; repo list or
metadata; releases; labels; workflow/run status; "what repos do I have access
to"; questions naming `github.com/owner/repo` or `owner/repo`.

## Important Functions

Use the registered tools — never raw `git`/`gh` in `shell_run`:

- `github_cli` — read-only `gh` (list/view/search/GET `api`)
- `github_cli_write` — mutating `gh` (create/edit/close/merge/…); **requires approval**

Pass `args` as the argument list **after** `gh` (do not include `gh` itself).
Optional `repo` as `owner/name` maps to `gh -R`.

### Read Operations

- List repos: `["repo", "list", "--limit", "30"]`
- Issues: `["issue", "list", "--limit", "20"]` / `["issue", "view", "123"]`
- PRs: `["pr", "list"]` / `["pr", "view", "45"]` / `["pr", "checks", "45"]`
- Repo metadata: `["repo", "view"]`
- API GET: `["api", "repos/{owner}/{repo}"]`

### Write Operations

- File an issue: draft title/body, get approval, then
  `github_cli_write` + `["issue", "create", "--title", "…", "--body", "…"]`.
  Reply with the issue URL from stdout.
- Edit/close/comment/merge: matching `issue`/`pr` subcommand via
  `github_cli_write` after approval.

Always end the turn with a short human summary of tool output (URLs, titles,
counts) — never a blank reply.

## Prefer dedicated tools only when they clearly fit

- Slack-sourced propose/execute: `propose_github_issue_mutation_from_slack` +
  `execute_github_issue_mutation`
- Investigation keyword issue search only: `search_github_issues`
- Work status / security alerts digests: existing GitHub workflow tools first;
  use `github_cli` for ad-hoc follow-ups

## Known Limitations

- The `gh` binary must be installed and on PATH where OpenSRE runs.
- Auth comes from OpenSRE GitHub config (`github_token` / `GITHUB_TOKEN` /
  `GH_TOKEN`), not ambient interactive `gh auth login` alone.
- GitHub Projects (v2) may return 403 depending on token/app permissions.
- Unknown `gh` shapes are treated as mutating (fail closed) — use
  `github_cli_write` after approval.
