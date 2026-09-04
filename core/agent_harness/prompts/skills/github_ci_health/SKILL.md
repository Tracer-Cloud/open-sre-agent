---
name: github-ci-health
description: >-
  Read-only GitHub CI health report for one repository, optionally narrowed to
  a branch or pull request.
recurring: unattended
---

# GitHub CI health

Use this skill to report failing CI checks for exactly one configured GitHub
repository. The schedule must supply `owner` and `repo`; it may supply either
`branch` or `pr_number`, never both.

The scheduled runner supplies a pre-fetched CI health block. Treat that block
as the complete source of truth and return it faithfully as the final report;
do not discover a broader organization or repository scope. Preserve every
failing check, link, age, responsible PR or branch, coverage notice, and repair
handoff in the block.

This workflow is read-only. Never call `fix_github_pr_ci`, `github_cli`,
`shell_run`, or any other mutating or external-command tool during unattended
execution. Repairs must be requested interactively and explicitly approved.

When offering this report as a recurring task interactively, use kind
`recurring_skill` and skill name `github-ci-health`. Pass the exact `owner` and
`repo`, plus at most one of `branch` or `pr_number`, to
`propose_scheduled_delivery` so confirmation preserves the repository scope.
