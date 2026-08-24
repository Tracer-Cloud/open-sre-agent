# Local CI Readiness — Mandatory Pre-Push Harness

This file is the **single source of truth** for required local validation before
any push or pull request. Repository-wide validation runs in GitHub Actions.
Feature- or package-specific validation required by an applicable contributor
guide supplements this harness and is intentionally not duplicated here.

<!--
Keep this document focused on required local checks and post-PR follow-through.
Do not add optional commands, CI implementation details, or tool-specific
procedures unless they change what contributors must do locally.
Automated contributors must not invent or run additional local CI steps beyond
the scoped checks below unless another applicable instruction or the user
explicitly requires them.
-->

## 0) Docs / process-only shortcut

If your diff is **only** documentation or contributor-process files, you may
skip the code-quality and test commands below.

Examples of files that qualify:

- `AGENTS.md`
- `CI.md`
- `CONTRIBUTING.md`
- `README.md`
- `docs/**/*.md`
- `docs/**/*.mdx`
- `docs/docs.json`

You may use the shortcut only when **all** changed files are non-runtime and
non-executable. If the diff touches application code, tests, build tooling,
dependency manifests, CI workflows, scripts, or anything with runtime impact,
run the normal harness.

For docs/process-only changes, the minimum required local check is:

```bash
git status --short
```

If you are unsure whether the shortcut applies, do **not** use it — run the
standard checks below.

## 1) Mandatory baseline checks (every code change that is not docs/process-only)

Run all of these first:

1. Clean working tree

   ```bash
   git status --short
   ```

   - No accidental untracked files
   - Never commit `.env` or secrets

2. Lint

   ```bash
   make lint
   ```

3. Format check

   ```bash
   make format-check
   ```

   If it fails:

   ```bash
   make format && make format-check
   ```

4. Typecheck

   ```bash
   make typecheck
   ```

## 2) Mandatory test harness (scope by touched modules)

Pick a focused test command for the modules you changed — do **not** default to
the full unit suite.

Map changed paths to targets using the `PathRule` entries in
[`.github/ci/test_scope_rules.py`](.github/ci/test_scope_rules.py):

- Rules with `always_escalate=True` identify high-blast-radius changes. Run the
  focused package and contract tests affected by the change.
- All other rules list a `test_targets` tuple — run those with
  `uv run python -m pytest <targets>`
- Changed files under `tests/` with no app rule run as-is

Use a focused `-k` filter when you only need a subset of a package.

## 3) Full suite runs in CI

The focused suite from section 2 is the required local test gate. Do not run
`make test-cov` as part of the normal local pre-push workflow; pull-request CI
runs the repository test suite in parallel shards.

List the focused tests you ran in the PR description. CI is the authoritative
repository-wide test result.

## 4) Session-store locked test suite (`session-store-locked`)

The `session-store-locked` CI job runs `tests/core/agent_harness/session/` with
`OPENSRE_SESSION_FILE_LOCK=1` and an explicit per-test timeout (`--timeout=120`).

- **Coverage**: Exercises cross-process write lock serialization across session
  files (used in scale-out multi-task deployments per `docs/deployment.mdx`).
- **Why the locked run is separate**: The file-locked path is a distinct code path
  from the default unlocked single-process path. Testing it in a dedicated CI lane
  with an explicit per-test timeout ensures cross-process concurrency bugs or hung
  process-spawning tests fail fast within minutes rather than silently hanging and
  blocking the PR queue.
- **Gating**: In `ci-gate`, this check is required for pull requests touching
  `core/agent_harness/session/persistence/`, and non-blocking for changes elsewhere.
  The existing unlocked same-process tests continue to run in the default test shard.

## 8) Post-PR follow-through

Opening a pull request does not end the validation cycle. Follow it through until
the repository's merge requirements are satisfied: required GitHub checks are
green, actionable human or automated review feedback (including Greptile) is
addressed, and resolved conversations are closed out.

A green check does not mean review feedback is clear. GitHub Code Quality and
GitHub Advanced Security can post inline review threads even when their checks
pass. After checks complete, and again after every push, inspect all unresolved
conversations and latest reviews, including feedback from
`github-code-quality` and `github-advanced-security`. Validate each finding. For
actionable feedback, push an appropriate fix, reply, and resolve the addressed
thread. For an incorrect or non-actionable finding, reply with the rationale
and resolve the thread without changing code.

After each completed PR update, once commits are pushed, the PR description is
current, and addressed threads are resolved, trigger a Greptile re-review by
following [CONTRIBUTING.md](CONTRIBUTING.md#greptile-code-review). Repeat until
Greptile reports 5/5 with no unresolved comments. Do not re-trigger while a
review is already running.

Use relevant built-in capabilities or locally installed skills, when available,
for PR monitoring, CI diagnosis, and review remediation rather than duplicating
tool-specific procedures in this document. Keep monitoring after each update;
do not treat creating or updating the PR as task completion. Validate review
suggestions before applying them, and rerun the appropriately scoped local
checks before pushing a fix.

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
