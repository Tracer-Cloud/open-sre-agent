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
| `automerge` label added, push, or reopen | Evaluate **that PR** only |
| Any other workflow's `check_suite` completes | Scan **all open PRs** with the `automerge` label |

The check-suite sweep exists so labeling while CI is still running still merges once checks go green. Without it, the label event can fire once (`check still running: …`) and never retry.

Merge criteria (see [`.github/scripts/automerge_pr.py`](scripts/automerge_pr.py)):

- Targets `main`, open, not draft, mergeable
- Has the `automerge` label
- Every reported check is green (`SUCCESS`, `SKIPPED`, or `NEUTRAL`), except this workflow's own check and Mintlify `vale-spellcheck`

## Fork PRs and first-time contributors

GitHub requires maintainer approval before `pull_request` workflows run on fork PRs from users who have never contributed to the repo.

| Event type | Runs without approval? | Examples |
| ---------- | ---------------------- | -------- |
| `pull_request` | **No** | CI, CodeQL, synthetic tests, turn checks |
| `pull_request_target` | **Yes** | Auto-merge, Greptile reminder |

**Recommended order for fork / first-time PRs:**

1. Approve workflows on the PR checks tab.
2. Wait for CI + Greptile 5/5.
3. Add the `automerge` label.

If you label before approving workflows, automerge may log `no status checks reported yet` until CI starts.

## If a labeled PR is stuck

Re-fire manually:

```bash
gh pr edit <n> --add-label automerge
```

Or remove and re-add the label, or push an empty commit with the label still present.

## Limits

- Uses `pull_request_target` so fork PRs can merge via `GITHUB_TOKEN`; the job only calls `gh` APIs (no PR head code execution).
- Draft PRs are skipped until marked ready for review.
- Remove the `automerge` label to cancel a pending merge.
