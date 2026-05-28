# CI Readiness — Mandatory Push/PR Harness

This file is the **single source of truth** for local CI readiness before any push or PR.

## 1) Mandatory baseline checks (every code change)

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

- Shared/core code changed (`app/utils/`, `app/state/`, `app/types/`, `app/pipeline/`, `app/nodes/`)
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

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
