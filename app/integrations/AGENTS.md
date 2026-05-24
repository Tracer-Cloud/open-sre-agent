# Integrations area

> **Scope:** `app/integrations/**` — when adding/changing integration config, verification, selectors, or wiring.
> **Parent:** repo-root `AGENTS.md` still applies.

## Purpose

Keep integration configuration and verification stable while separating vendor API logic into service clients and tool-facing adapters.

## Commands

- Integration tests: `uv run pytest tests/integrations/ -v`
- Verification harness: `make verify-integrations`
- Quality baseline: `make lint && make format-check && make typecheck`

## Conventions

- Normalize integration config first; keep one consistent runtime shape.
- Put vendor API clients in `app/services/<vendor>/` (legacy `app/integrations/clients/` exists; avoid adding new clients there).
- Keep API-specific behavior in service clients; tool layers handle extraction/formatting.
- Prefer stateless clients or explicit lifecycle management.
- Maintain typed, predictable client responses and actionable error payloads.

## If X → Y

- If adding a new integration → update integration config + catalog + verification path + tests + docs in one PR.
- If integration behavior/config changes → update `tests/integrations/` and rerun `make verify-integrations`.
- If integration introduces user-visible tool behavior → update corresponding tool tests/docs too.

## Checklist

- Config normalization + validation paths updated.
- Verification path implemented for local checks when required.
- Tool wiring consumes normalized shape only.
- Docs and docs navigation (`docs/docs.json`) updated when needed.

## Footguns

- Don’t inline vendor HTTP logic inside integration config modules.
- Don’t ship integration changes without verification + test coverage.
