# GitHub Actions (maintainer reference)

Internal notes for repository automation under `.github/workflows/`. Not published on the docs site.

## Auto-merge (`automerge.yml`)

Opt-in squash merge when CI is green. Trigger: add the **`automerge`** label to a PR targeting `main`.

### When to use

1. Greptile is **5/5** and review feedback is addressed.
2. You are ready to merge and do not want to wait on CI manually.

### Flow

1. Add the `automerge` label on GitHub (or `gh pr edit <n> --add-label automerge`).
2. [`.github/scripts/automerge_pr.py`](../scripts/automerge_pr.py) runs on label add, new pushes, or check-suite completion.
3. The PR is squash-merged with branch delete when every reported check is green:
   - GitHub Actions **CheckRun** items (`SUCCESS`, `SKIPPED`, `NEUTRAL`)
   - Legacy commit **StatusContext** items (`SUCCESS` only)

Push again with the label still on to re-merge after CI. Remove the label to cancel.

### Limits

- **Upstream branches only** — fork PRs still need a manual merge (`GITHUB_TOKEN` cannot write on fork heads).
- **Greptile is not a GitHub check** — the label does not wait for Greptile; add it only when review is done.
- **Draft PRs** — skipped until marked ready for review.

## Other workflows (pointers)

| Workflow | Purpose |
| -------- | ------- |
| [`ci.yml`](ci.yml) | PR/push quality gates and sharded pytest |
| [`ci-labels-windows.yml`](ci-labels-windows.yml) | Optional Windows CI (`ci:windows` label) |
| [`codeql.yml`](codeql.yml) | CodeQL security analysis |
| [`greptile-pr-reminder.yml`](greptile-pr-reminder.yml) | Greptile review nudge on PR open |
| [`celebrate-merged-pr.yml`](celebrate-merged-pr.yml) | Post-merge celebration comment |
| [`good-first-issue-assign.yml`](good-first-issue-assign.yml) | Auto-assign good first issues |
| [`release.yml`](release.yml) | Release builds and artifacts |

See [CI.md](../../CI.md) for local parity commands before push.
