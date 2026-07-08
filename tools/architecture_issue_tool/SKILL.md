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
   `repo` from GitHub sources or user input. Use `include_baselines=True` for a
   full debt inventory (including import-linter baselines); use `False` for
   actionable-only findings outside baselines.
2. Interpret results. Summarize by severity (`p0`, `p1`, `p2`). Cite repo-relative
   paths from each violation's `evidence`. Treat `scan_summary.warnings` as
   incomplete coverage, not proof of a clean repo.
3. Propose, do not execute. Return `refactor_tasks` to the user. Never auto-apply
   code changes or batch unrelated violations into one mega-refactor.
4. GitHub issues are separate. To file tasks, build proposals from
   `suggested_issue_body` and call `execute_github_issue_mutation` (or
   `propose_github_issue_mutation_from_slack`) only after explicit user approval.
   Pass the same `owner`/`repo` as the audit.
5. Limitations. Layer and direct-import checks require import-linter config (or
   `.github/ci/check_direct_imports.py`) in the target repo. Oversized-file,
   compatibility-shim, and placement heuristics run when relevant directories
   exist in the clone. Repos without import-linter config still get local scans;
   layer checks are skipped with a warning in `scan_summary`.
