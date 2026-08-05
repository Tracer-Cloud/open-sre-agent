---
name: github-ci-fix
description: Use when the user asks OpenSRE to fix failing GitHub PR CI, GitHub Actions checks, failing pull request checks, a broken PR branch, or to fix CI and push back to the branch.
tools:
  - fix_github_pr_ci
---

# GitHub PR CI Fix

Use `fix_github_pr_ci` for GitHub pull request CI remediation requests, not
`github_cli` or `shell_run`.

Rules:

- Pass `pr_url` when the user provides a GitHub pull request URL.
- Pass `owner`, `repo`, and `pr_number` when the user names a repo and PR number.
- If no repo is named, omit `owner` and `repo`; the tool uses the current
  checkout's GitHub origin.
- The tool inspects failing GitHub Actions checks, fixes the local checkout,
  commits, and pushes to the PR's existing head branch. It does not open a new PR.
- Fork PR branches are refused by the tool because OpenSRE only pushes to
  branches in the same repository.
- The tool owns CI log inspection, fix execution, branch checkout, commit, and
  push. Do not run a raw `gh` workflow around it.
- If the tool returns `response_text`, output exactly that text and stop.
- If no fix is produced, keep the reply to one short line from `error`; do not
  say "next steps", add numbered options, list example commands, or ask a broad
  follow-up question.
