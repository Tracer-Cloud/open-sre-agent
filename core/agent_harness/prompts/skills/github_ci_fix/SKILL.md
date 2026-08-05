---
name: github-ci-fix
description: >-
  Fix failing GitHub PR CI / Actions checks via fix_github_pr_ci and push to
  the existing PR head
---
══════════════════════════════════════════════════════════
GITHUB PR CI FIX SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE:
- The user asks to fix failing CI, broken GitHub Actions checks, failing PR
  checks, or a red pull request branch.
- The user says "fix CI on this PR", "fix the CI of PR 123 and push", "repair
  the failing checks on owner/repo#123", or provides a GitHub pull request URL
  and asks for CI/check fixes.

USE THIS TOOL:
- `fix_github_pr_ci`

DO NOT USE THIS SKILL FOR:
- Ordinary PR reads, comments, closes, merges, labels, or issue work. Use
  `github_cli`.
- Security alert remediation. Use `fix_github_security_alert`.
- Live incident RCA. Use `investigation_start`.

HARD RULES:
- For a GitHub PR URL, call:
  `fix_github_pr_ci(pr_url="<url>")`
- For `owner/repo#123` or "PR 123 in owner/repo", call:
  `fix_github_pr_ci(owner="owner", repo="repo", pr_number=123)`
- If no owner/repo is named, omit both and let the tool use the current
  checkout's GitHub origin.
- Never use `github_cli` or `shell_run` to run raw `gh pr checks`, `gh run view`,
  checkout, commit, or push for this workflow. The CI fixer owns PR metadata,
  failing-check log inspection, fix execution, branch safety, commit, and push.
- The tool pushes to the existing PR head branch after approval. Do not ask the
  user whether to open a new PR.
- If the tool returns `response_text`, output exactly that text and stop.
- If `error_kind` is set, reply in one short line from `error`. Do not say
  "next steps", do not add numbered options, do not list example commands, and
  do not ask a broad follow-up question.

Compact examples:
1) "fix CI on https://github.com/Tracer-Cloud/opensre/pull/4597 and push"
   → fix_github_pr_ci(pr_url="https://github.com/Tracer-Cloud/opensre/pull/4597")
2) "fix failing checks on Tracer-Cloud/opensre#4597"
   → fix_github_pr_ci(owner="Tracer-Cloud", repo="opensre", pr_number=4597)
3) "the current PR CI is failing, fix and push"
   → fix_github_pr_ci()
