# CI Readiness — Mandatory Push/PR Harness

This file is the **single source of truth** for local CI readiness before any push or PR.

## 0) Docs / process-only shortcut

If your diff is **only** documentation or contributor-process files, you may
skip the code-quality and test commands below.

Examples of files that qualify:

- `AGENTS.md`
- `CI.md`
- `CONTRIBUTING.md`
- `README.md`
- `TESTING.md`
- `TOOL_INTEGRATION_CHECKLIST.md`
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

**Recommended — run this instead of manually looking up the table below:**

```bash
make test-scope
```

`make test-scope` reads `git diff` against `main`, maps each changed path to
its test target(s) using [`infra/ci/test_scope_rules.py`](infra/ci/test_scope_rules.py),
and runs the minimal `pytest` invocation. It escalates automatically to
`make test-cov` when shared/core code is touched or 3+ app areas change.
Pass `ARGS=--dry-run` to preview without running.

### Manual lookup (reference only)

If you prefer to pick the command yourself, or need a focused `-k` filter,
see the `PathRule` entries in [`infra/ci/test_scope_rules.py`](infra/ci/test_scope_rules.py).
Rules with `always_escalate=True` map to `make test-cov`; all others list their
`test_targets` tuple. Changed files under `tests/` with no app rule run as-is.

## 3) Escalation rules (must run full unit CI suite)

Run `make test-cov` (instead of only targeted tests) when any of these are true:

- Shared/core code changed (`utils/`, `core/domain/state/`, `core/domain/types/`, `core/orchestration/`, `app/nodes/`)
- 3+ app areas changed in one diff
- New files with unclear blast radius
- Cross-cutting refactor
- You are unsure test scope is sufficient

```bash
make test-cov
```

## 4) Conditional checks

If integration config, integration wiring, or related tools changed, also run:

```bash
make verify-integrations
```

## 5) Optional extra confidence

You may run `make check` as a final pass, but it is heavier (`test-full`) than the required harness.

## 6) Routing tests

Routing live tests always run with live coverage enabled. Do not use deselection filters like `-k "not live_llm"`. Fix failures by improving planner/tool correctness or updating fixtures only when behavior changes are explicitly approved.

In CI, [`.github/workflows/routing-live.yml`](.github/workflows/routing-live.yml) runs two jobs on same-repo PRs and post-merge `main` pushes: a no-LLM `routing-checks` gate (deterministic routing + fixture integrity, `-m "not live_llm"`) and the sharded `routing-live` job (8 shards, live coverage). The no-LLM gate is a fast guardrail, not a substitute for live coverage.

## 7) CI-only tests

Some paths require live infrastructure and are excluded from `make test-cov`:

- Kubernetes / EKS scenarios (`tests/e2e/`)
- Chaos Mesh workflows (`tests/chaos_engineering/`)
- Docker-dependent Grafana stack tests

Mark CI-only tests with the appropriate pytest marker or place them in the correct folder so they do not run locally by default.

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
