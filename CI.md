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

Do **module-scoped tests by default**.

### A. Identify changed files

```bash
git diff --name-only $(git merge-base HEAD main)
```

(Use `git diff --name-only HEAD~1` only for quick single-commit checks.)

### B. Run matching tests for touched paths

| Changed path | Run |
|---|---|
| `app/tools/` | `uv run pytest tests/tools/ -v` *(or `-k <keyword>` for focused runs)* |
| `app/services/` | `uv run pytest tests/services/ tests/tools/ -v` *(or add `-k <service>`)* |
| `app/integrations/` | `uv run pytest tests/integrations/ -v` |
| `app/integrations/llm_cli/` | `uv run pytest tests/integrations/llm_cli/ -v` |
| `app/integrations/opensre/` | `uv run pytest tests/integrations/opensre/ -v` |
| `app/pipeline/` | `make test-cov` |
| `app/nodes/` | `make test-cov` |
| `app/agent/` or `app/agents/` | `uv run pytest tests/agent/ tests/agents/ -v` |
| `app/cli/` | `uv run pytest tests/cli/ -v` |
| `app/entrypoints/` | `uv run pytest tests/entrypoints/ -v` |
| `app/remote/` | `uv run pytest tests/remote/ -v` |
| `app/sandbox/` | `uv run pytest tests/sandbox/ -v` |
| `app/deployment/` | `uv run pytest tests/deployment/ tests/app/deployment/ -v` |
| `app/delivery/` | `uv run pytest tests/delivery/ -v` |
| `app/guardrails/` | `uv run pytest tests/test_guardrails/ -v` |
| `app/masking/` | `uv run pytest tests/masking/ -v` |
| `app/analytics/` | `uv run pytest tests/analytics/ -v` |
| `app/auth/` | `uv run pytest tests/app/auth/ -v` |
| `app/hermes/` | `uv run pytest tests/hermes/ tests/synthetic/hermes_rca/ -v` |
| `app/watch_dog/` | `uv run pytest tests/watch_dog/ -v` |
| `app/types/` | `make test-cov` |
| `app/state/` | `make test-cov` |
| `app/utils/` | `make test-cov` |
| `app/webapp.py` | `uv run pytest tests/test_webapp.py -v` |
| Other `app/` paths (no direct mapping above) | `make test-cov` |
| `tests/...` only | Run the exact changed test files/directories |
| `pyproject.toml`, `uv.lock`, `pytest.ini`, `Makefile` | `make test-cov` |

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
