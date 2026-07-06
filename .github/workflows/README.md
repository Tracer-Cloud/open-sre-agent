# GitHub Actions (maintainer reference)

Internal notes for repository automation under `.github/workflows/`. Not published on the docs site.

## Workflows

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
