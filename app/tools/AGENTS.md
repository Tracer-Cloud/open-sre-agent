# Tools area

> **Scope:** `app/tools/**` — when adding or changing investigation/chat tools.
> **Parent:** repo-root `AGENTS.md` still applies.

## Purpose

Define tool implementation, registration, and schema conventions used by the planner/runtime.

## Commands

- Targeted tests: `uv run pytest tests/tools/ -v`
- Optional focused run: `uv run pytest tests/tools/ -k <keyword> -v`
- Quality baseline before PR: `make lint && make format-check && make typecheck`

## Conventions

- Use one of two patterns:
  1. Function tool with `@tool(...)` (preferred for simple tools)
  2. `BaseTool` subclass (for richer behavior)
- `source` is required and must be a valid `EvidenceSource` literal.
- Keep tool logic self-contained; move transport/client logic to `app/services/`.
- If a tool is needed in both surfaces, set `surfaces=("investigation", "chat")`.
- `BaseTool` classes must be instantiated at module scope for auto-discovery.
- Return structured `dict` output; include clear error payloads on failures.

## If X → Y

- If adding a new tool module/package → keep it under `app/tools/`; do not edit registry skip lists.
- If tool schema/API changes → update `tests/tools/` and docs in `docs/` in the same PR.
- If introducing a new evidence source → update the corresponding typed source definitions/state contracts.

## Checklist

- Tool metadata (`name`, `description`, `source`, `input_schema`) is complete.
- Availability and param extraction are deterministic and tested.
- Runtime behavior has regression coverage.
- Docs reflect usage, parameters, and examples.

## Footguns

- Module names ending in `_test` are skipped by auto-discovery.
- Don’t hide API failures; surface actionable errors for planner/user debugging.
