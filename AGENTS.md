# Tracer Agent Instructions (Root)

> **Scope:** entire repository.
> **Precedence:** this file is the global baseline. Nearest nested `AGENTS.md` adds path-specific rules.

## Purpose

Keep this root file slim and portable. Put detailed, path-scoped rules in nested `AGENTS.md` files.

## Build and run (always applicable)

- Install/bootstrap: `make install`
- Preferred dev execution: `uv run opensre …`
- Python commands: `uv run python …`

## Quality commands

- Lint: `make lint`
- Format check: `make format-check`
- Auto-format: `make format`
- Type check: `make typecheck`
- Unit suite: `make test-cov`
- Real-alert RCA: `make test-rca`
- Full gate (heavier): `make check`

## Push/PR gate (mandatory)

Before any push or PR, follow **[CI.md](CI.md)** end-to-end.

- `CI.md` is the source of truth for readiness.
- Do not skip required checks.

## AGENTS graph (source of truth by path)

| Path | Guidance file |
| --- | --- |
| `app/tools/**` | `app/tools/AGENTS.md` |
| `app/integrations/**` | `app/integrations/AGENTS.md` |
| `app/integrations/llm_cli/**` | `app/integrations/llm_cli/AGENTS.md` |
| `app/pipeline/**`, `app/agent/**`, `app/state/**`, `app/delivery/**` | `app/pipeline/AGENTS.md` |
| `app/services/**` | `app/services/AGENTS.md` |
| `app/cli/interactive_shell/**` | `app/cli/interactive_shell/AGENTS.md` (+ deeper runtime/routing files) |
| `tests/**` | `tests/AGENTS.md` (focused on e2e RCA spec principles) |

## Universal rules (if X → do Y)

- If core agent/pipeline behavior changes → run `make test-cov` and `make typecheck`.
- If integration wiring/config changes → run `make verify-integrations` and update `tests/integrations/`.
- If tool schema/API changes → update relevant docs + `tests/tools/`.
- If feature behavior/flags/config changes → update docs in the same PR.
- If adding/renaming docs pages (`.mdx`) → add/update `docs/docs.json` entry.
- If adding tests → place under `tests/`, not `app/`.
- If working on routing scenario coverage → do **not** deselect live tests with filters like `-k "not live_llm"`.

## Testing policy highlights

- Use module-scoped test commands from `CI.md` based on touched paths.
- Escalate to `make test-cov` for shared/core or unclear blast-radius changes.
- Do not bypass live routing behavior with deterministic shortcuts just to satisfy tests.

## Footguns

- Never commit secrets or `.env`; use `.env.example`.
- Some e2e/chaos/k8s tests require live infra and won’t pass in minimal local envs.
- Mintlify navigation is driven by `docs/docs.json`; new doc files are not auto-listed.
- Investigation tool-calling schemas must be provider-safe; see `docs/investigation-tool-calling.md`.

## Repository map (quick index)

- `app/` core runtime: CLI, tools, integrations, pipeline, services, state.
- `tests/` capability-based test suites.
- `docs/` user docs and integration guides.
- `.github/` CI workflows and PR templates.
- `CI.md` required push/PR checklist.
- `CONTRIBUTING.md` contributor workflow.
