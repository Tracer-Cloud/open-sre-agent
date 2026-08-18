---
name: github-cli
description: >-
  GitHub issues/PRs/repos/releases via github_cli (not star history, not CI
  fix, not security-alert remediation) and also use it for GitHub analytics
---
══════════════════════════════════════════════════════════
GITHUB CLI SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE (call github_cli; do NOT assistant_handoff):
- Create / list / view / edit / close / comment / assign / label GitHub issues
- Create / list / view / close / merge / comment / checks on pull requests
- Repo list/view, releases, labels, workflow runs, search, or gh api
- github.com/owner/repo URLs or "open an issue / merge this PR" requests
- Follow-ups like "create an issue from that" after a prior scan/report

Do NOT use this skill for:
- Live incident RCA (investigation_start) — including multi-source asks that
  name github issues alongside Sentry/PostHog/Datadog while diagnosing a crash
- Star history / day-by-day stars / stars gained / star velocity — emit
  assistant_handoff so gather can call get_github_star_history. Do NOT use
  github_cli, gh api stargazers, or shell_run for these; paginated stargazer
  scans via gh routinely undercount or report false zeros.
- Observability lookups (Sentry/Datadog/Grafana/PostHog) — those stay handoffs
- Slack → GitHub propose/execute mutations (workflow tools)
- Architecture audit (architecture_* tools + architecture audit skill)
- GitHub PR CI remediation / failing checks fixes / "fix CI and push" — use
  fix_github_pr_ci when available
- GitHub security alert remediation / vulnerable dependencies / code-scanning
  fixes — use fix_github_security_alert when available

HARD RULES:
- Prefer github_cli over shell_run / !gh / raw gh.
- Never emit assistant_handoff for these requests just to "let gather create the
  issue" — github_cli is action-only and will not run in gather.
- Never call github_cli with auth / extension / workflow / run / secret /
  codespace / ssh-key / gpg-key / config — those are blocked (token leakage /
  CI code execution / secret mutation).
- Pass args after the gh binary; optional repo as owner/name → -R.
- After the tool returns, end with a short chat-like reply from result.summary.
  Simple confirms (create/close/merge + URL/#n): plain prose, no markdown.
  Multi-item reads: light markdown that still reads like chat (short lead-in +
  bullets; prefer bullets over tables/headers). No GraphQL dumps. No "I found:"
  for simple confirms.

Compact examples:
1) "create an issue titled X with body Y"
   → github_cli(args=["issue", "create", "--title", "X", "--body", "Y"], repo?)
2) "list open PRs" → github_cli(args=["pr", "list", "--state", "open"], repo?)
3) "merge PR 45 with squash auto" →
   github_cli(args=["pr", "merge", "45", "--squash", "--auto"], repo?)
4) "comment on issue 12: …" →
   github_cli(args=["issue", "comment", "12", "--body", "…"], repo?)
