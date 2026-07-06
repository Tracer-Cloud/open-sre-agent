# Auto-merge (maintainer reference)

Internal notes for the `automerge` label and [`.github/workflows/automerge.yml`](workflows/automerge.yml). Not published on the docs site.

## When to add the label

1. Greptile is **5/5** and review feedback is addressed.
2. CI checks are green (or about to finish).
3. You are ready to squash-merge without waiting at the keyboard.

Greptile is **not** a GitHub check — the workflow does not wait for it. Add the label only after review is done.

## How it works

| Trigger | What runs |
| ------- | --------- |
| `automerge` label added, push, or reopen | Try merge on **that PR** |
| CI / CodeQL / synthetic / turn-check workflows finish on a PR branch | Look up the open PR for that branch and try merge again |

Labeling while CI is still running is fine: the label event may log `check still running`, then the PR's own workflow completion retries automerge.

## Fork PRs and first-time contributors

GitHub requires maintainer approval before `pull_request` workflows run on fork PRs from first-time contributors.

**Recommended order:** approve workflows → wait for CI + Greptile 5/5 → add `automerge`.

## If a labeled PR is stuck

```bash
gh pr edit <n> --add-label automerge
```

## Limits

- Uses `pull_request_target` so fork PRs can merge via `GITHUB_TOKEN`; the job only calls `gh` APIs (no PR head code execution).
- Draft PRs are skipped until marked ready for review.
- Remove the `automerge` label to cancel a pending merge.
