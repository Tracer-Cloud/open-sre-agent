# Routing package rules

These instructions apply to `app/cli/interactive_shell/routing/` and all
subdirectories. Parent `AGENTS.md` files still apply.

## Readability and helper-function policy

## Compatibility-shim policy

- Do **not** keep compatibility-only forwarding modules (files that only
  re-export symbols from a new location) in `routing/`.
- After all local imports/tests are migrated, remove the shim in the same
  change rather than leaving it behind.
- Prefer one canonical import path per routing concern; avoid dual old/new
  module paths that add maintenance noise.

- Do **not** introduce tiny wrapper helpers that only forward a call, rename a
  variable, or return a trivial tuple/value.
- As a hard rule, avoid creating helper functions that are ~1-3 lines unless
  they satisfy one of the allowed exceptions below.
- Prefer keeping short, linear logic inline in the main routing flow when the
  helper would force readers to jump around the file.

### Allowed exceptions

Small helpers are allowed only when at least one is true:

- **Boundary isolation:** wraps exception handling, I/O, or cross-module
  boundary behavior that should be isolated for safety.
- **Reuse:** used in multiple call sites (not just one).
- **Domain naming:** captures a routing concept that is non-obvious inline.
- **Test seam:** creates a stable seam needed for deterministic tests.

### Review checklist

Before adding any helper in routing modules, ask:

1. Does this reduce cognitive load, or just split obvious logic?
2. Would inline code be clearer than jumping to another function?
3. Is there a concrete exception from the allowed list above?

If all answers are weak, keep the logic inline.

## Test placement policy for routing

- Routing tests must be co-located under `app/cli/interactive_shell/routing/tests/`.
- Do **not** move routing tests back under `tests/cli/interactive_shell/routing/`.
- When adding a new routing-phase test file, place it under
  `app/cli/interactive_shell/routing/tests/` and keep routing test fixtures
  under that subtree.

## Routing test ownership and file layout (canonical)

| File | Ownership | Scope |
| --- | --- | --- |
| `app/cli/interactive_shell/routing/tests/test_router_contracts.py` | Routing package owners | Deterministic slash/alias routing contracts |
| `app/cli/interactive_shell/routing/tests/test_router_live_prompts.py` | Routing package owners | Curated high-signal live `route_input` prompt contracts |
| `app/cli/interactive_shell/routing/tests/test_dataset_integrity.py` | Routing package owners | Dataset/schema/no-mocks guardrails |
| `tests/cli/interactive_shell/orchestration/test_llm_intent_classifier.py` | Orchestration owners | Classifier internals (sanitization + live cache/override behavior) |
| `app/cli/interactive_shell/routing/tests/router_contracts.yml` | Routing package owners | Deterministic slash/alias command contracts |
| `app/cli/interactive_shell/routing/tests/router_live_prompts.yml` | Routing package owners | Canonical live prompt corpus for shardable contract runs |

## Routing test isolation policy (no mocks)

- Do **not** use `unittest.mock`, `patch`, `MagicMock`, or equivalent mocking
  primitives in routing tests.
- Do **not** stub or monkeypatch the LLM client path in routing tests.
- Do **not** stub or monkeypatch `llm_phase_route` in routing tests.
- Routing contract tests must exercise the real routing stack
  (`route_input` -> `handle_message_with_agent` -> classifier/fallback) and
  rely on curated prompts instead of synthetic mocked return values.

## Important routing decisions (locked)

- Keep `route_input` as a strict two-branch flow only:
  1) `resolve_cli_command(...)`; else 2) `handle_message_with_agent(...)`.
- `resolve_cli_command(...)` owns deterministic command routing only
  (slash-prefixed commands and bare command aliases).
- `handle_message_with_agent(...)` owns non-command routing and should stay
  linear: LLM intent classifier -> default `cli_agent`.
- Regex fallback has been intentionally removed from routing. Do **not**
  re-introduce `regex_fallback`/`routes/route_regex_fallback`-style phases
  unless there is an explicit product decision to restore them.
- Keep the LLM intent classifier canonical in orchestration (`app/cli/interactive_shell/orchestration/llm_intent_classifier.py`);
  routing can wrap/import it, but should not duplicate classifier logic.
- Preserve routing decision observability contracts used in tests:
  `fallback_reason` semantics and `matched_signals` (`cli_agent_action_plan`, etc.).

## Routing test execution requirements (locked)

- Routing tests are part of the default CI/CD flow; do **not** move them to
  optional-only jobs.
- Keep deterministic routing contracts (`test_router_contracts.py` and
  `test_dataset_integrity.py`) in the default PR CI flow.
- Run live-LLM suites (`test_router_live_prompts.py`,
  `tests/cli/interactive_shell/orchestration/test_llm_intent_classifier.py`)
  in the post-merge sharded workflow.
- Execute routing suites with heavy parallelism (`pytest-xdist`, e.g. `-n auto`)
  in both local and CI environments.
- Local developer goal: the live-LLM routing contract suite should be runnable
  in roughly 20 seconds on a typical dev machine.
- If runtime drifts above the target, reduce/curate prompt cases first (keep
  coverage signal high), then tune worker parallelism; avoid weakening core
  deterministic routing coverage.
