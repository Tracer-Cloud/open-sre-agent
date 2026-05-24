# Investigation pipeline area

> **Scope:** `app/pipeline/**`, `app/agent/**`, `app/state/**`, `app/delivery/**`.
> **Parent:** repo-root `AGENTS.md` still applies.

## Purpose

Preserve a clear, staged investigation flow and strong state contracts across extraction, context, investigation, and delivery.

## Commands

- Core suite: `make test-cov`
- Type safety: `make typecheck`
- Optional confidence pass: `make check`

## Coordinator and key files

- `app/pipeline/pipeline.py`: stage ordering and high-level orchestration.
- `app/pipeline/runners.py`: `run_investigation`, `run_chat`, and streaming entry points.
- `app/agent/`: extract/context/investigation/chat behavior.
- `app/state/`: persisted state contracts crossing stage boundaries.
- `app/delivery/`: publishing and output integration.

## Conventions

- Keep stages focused: read full state, return partial state updates.
- Prefer typed state additions whenever new keys cross stage boundaries.
- Use tracing/progress helpers consistently for externally visible orchestration.
- Keep orchestration logic explicit; avoid hidden side effects across stages.

## If X → Y

- If stage order or branching changes → update `app/pipeline/pipeline.py` + path tests.
- If new persisted fields are introduced → update state models/validators and tests.
- If user-visible behavior/config changes → update docs in the same PR.

## Checklist

- Stage responsibilities remain single-purpose.
- State schema changes are typed and validated.
- `run_investigation` / streaming paths are regression-tested.

## Footguns

- Don’t add cross-stage data by implicit dict mutation without state-contract updates.
- Don’t bypass live planner behavior in routing-related scenario fixes.
