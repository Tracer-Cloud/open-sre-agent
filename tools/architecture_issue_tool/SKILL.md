---
name: architecture-audit
description: Audit a GitHub repository for architecture violations and turn findings into atomic refactor tasks and optional GitHub issues.
tools:
  - find_architecture_violations
---

# Architecture Audit

Use this workflow when the user asks for an architecture audit, layer violations,
tech-debt scan, or refactor task breakdown on a specific GitHub repository.

1. Read before reporting. Call `find_architecture_violations` with `owner` and
   `repo`. Use `include_baselines=True` for full debt inventory; `False` for
   actionable-only findings outside baselines.
2. Interpret results. Use `scan_summary.severity_counts`, `kind_counts`,
   `categories_skipped`, and `coverage_complete`. Cite repo-relative paths from
   each violation's `evidence`. Warnings and skipped categories mean incomplete
   coverage, not a clean repo.
3. Write the Markdown report. The tool returns JSON only. Your user-facing reply
   must be a Markdown audit report following `AUDIT_REPORT.md` in this directory.
   Synthesize themes from `violations` and `refactor_tasks`. Do not echo raw JSON
   or say the repo looks clean when `coverage_complete` is false.
4. Propose, do not execute. `refactor_tasks` support issue filing; the main
   deliverable is the Markdown report. Never auto-apply code changes.
5. GitHub issues are separate. Use `suggested_issue_body` and
   `execute_github_issue_mutation` only after explicit user approval.
6. Limitations. Import/layer checks are polyglot tree-sitter scans with tool-owned
   contracts — no import-linter config or Python requirement in the target repo.
   Oversized-file, shim, and placement checks are Python/OpenSRE-specific and skip
   gracefully when the clone lacks relevant files or layout markers.
