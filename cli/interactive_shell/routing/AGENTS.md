# Routing package rules

These instructions apply to `cli/interactive_shell/routing/` and all
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

- Routing tests must be co-located under `cli/interactive_shell/routing/tests/`.
- Do **not** move routing tests back under `tests/cli/interactive_shell/routing/`.
- When adding a new routing-phase test file, place it under
  `cli/interactive_shell/routing/tests/` and keep routing test fixtures
  under that subtree.

## Routing test ownership and file layout (canonical)

| File | Ownership | Scope |
| --- | --- | --- |
| `cli/interactive_shell/routing/tests/test_routing_scenarios.py` | Routing package owners | Canonical runner: deterministic routing, live classification, action planning, turn-execution oracles |
| `cli/interactive_shell/routing/tests/test_routing_fixture_integrity.py` | Routing package owners | Scenario-tree/schema/no-mocks guardrails |
| `cli/interactive_shell/routing/tests/scenario_loader.py` | Routing package owners | Load `scenarios/<behavior_class>/<id>/{scenario.yml,answer.yml}` |
| `cli/interactive_shell/routing/tests/scenarios/**/scenario.yml` | Routing package owners | Input world: prompt, session, capabilities, intent metadata |
| `cli/interactive_shell/routing/tests/scenarios/**/answer.yml` | Routing package owners | Expected behavior: route, policy, planned/executed actions, response contract |

## Scenario schema and `available_capabilities` semantics

Scenario fixtures are intentionally minimal: the loader (`scenario_loader.py`)
parses only fields the runner asserts on. Do **not** re-add decorative metadata.

- **Removed fields (do not re-add):** `risk_level`, `tier`,
  `session.remote_connected`, and `input.surface` were parsed-but-unused and have
  been dropped from the schema and every fixture. `title` and `notes` remain as
  human-only documentation.
- **`available_capabilities` is a three-state, production-faithful knob.** It
  constrains which planner tools the live oracle offers. Its default mirrors
  production: `ReplSession()` carries no capability constraints, so every tool is
  enabled. For each surface (`slash_commands`, `cli_commands`, `synthetic_suites`):
  - **omit the key (or omit the whole block)** → the tool stays enabled (the
    production default). This is the right choice for almost every scenario.
  - **explicit empty list `[]`** → the tool is explicitly disabled (hidden from
    the planner specs and blocked at dispatch). Use only when a scenario genuinely
    needs that surface off to test a specific path.
  - **non-empty list** → an allowlist: the tool is enabled and the action
    normalizer drops proposed actions outside the list.
- **Do not re-introduce all-empty `available_capabilities` blocks.** A block that
  disables all three surfaces is the old redundant boilerplate that predated the
  production-faithful default; it is rejected by
  `test_available_capabilities_blocks_are_not_redundant_boilerplate`. Omit the
  block instead.

## Routing test isolation policy (no mocks)

- Do **not** use `unittest.mock`, `patch`, `MagicMock`, or equivalent mocking
  primitives in routing tests.
- Do **not** stub or monkeypatch the LLM client path in routing tests.
- Routing contract tests must exercise the real routing stack
  (`route_input` -> `handle_message_with_agent`) and rely on curated prompts
  instead of synthetic mocked return values.

## Important routing decisions (locked)

- Keep `route_input` as a **single-branch** entrypoint: every turn returns a
  `handle_message_with_agent` decision. Do **not** add command/slash/help/alert
  branches or any other top-level routing phases here.
- Deterministic command dispatch is an **internal fast path of the agent**, not
  a routing branch. `handle_message_with_agent` detects literal slash commands,
  bare command aliases, and `opensre investigate` quick-starts via
  `handle_message_with_agent/command_dispatch/` and dispatches them directly
  (no LLM); everything else falls through to LLM action planning + assistant.
- The runtime (`runtime/dispatch.py`) may reuse
  `command_dispatch.deterministic_command_text` for terminal-UI concerns only
  (spinner suppression and exclusive-stdin gating). This is a presentation
  concern and must not re-introduce a routing branch.
- Regex fallback has been intentionally removed from routing. Do **not**
  re-introduce `regex_fallback`/`routes/route_regex_fallback`-style phases
  unless there is an explicit product decision to restore them.
- **The LLM action planner is the sole tool selector for non-command turns.**
  There is no regex/keyword intent inference and no deterministic
  natural-language → action mapping in `orchestration/`. The former
  `slash_commands/deterministic_action_mapper.py`, `intent_parser` regex
  patterns/extractors, and the regex planner postprocessing overrides were
  removed. Do **not** reintroduce them: change tool selection by editing the
  planner system prompt (`llm_action_planner/constants.py`) and the per-tool
  descriptions in `orchestration/tools/*`, never by adding pattern matching.
  Tool-call argument *validation* (shape/availability checks in
  `llm_action_planner/normalization.py` and `parsing.py`) is allowed; intent
  *classification* by regex is not.
- When the planner LLM is unavailable or the prompt overflows, hand off to the
  conversational `assistant` — do not guess an action deterministically and do
  **not** deny the turn.
- **No planning-stage fail-closed safeguard (v0.1).** There is no "I couldn't
  safely decide actions" denial. Every terminal action is read-only, so an
  unmatched, ambiguous, or chatty clause never blocks a turn: the planner runs
  the clauses it can map and the rest fall through to the assistant. The former
  `denied` decision, the `mark_unhandled` planner tool, the `UNHANDLED:` text
  convention, and `render_plan_denied` were removed for this reason. Do **not**
  reintroduce a planning-stage denial; if write/mutating actions are ever added,
  gate them with an execution-stage confirmation (see
  `orchestration/execution_policy.py`), not a planner denial. The legacy
  `fail_closed`, `has_unhandled_clause`, and `route.expected_signals` fixture
  fields were removed (the oracle never asserted on them); the policy block now
  carries a single `executes_terminal_action` boolean. See the `Answer` and
  `AnswerPolicy` docstrings in `tests/scenario_loader.py` for the two execution
  paths a turn can take (planner→dispatch vs conversational tool-gathering) and
  which fixture fields cover which.
- Scenario fixtures use a single ``tool_actions`` list (not separate
  ``executed_actions`` / ``gathered_tools_contract`` blocks). Each entry has
  ``surface: dispatch`` (terminal action shape: slash, investigation, …) or
  ``surface: gather`` with ``expect`` (``not_called``, ``called``, ``call_any``,
  ``valid_data``, ``valid_data_any``). Handoff-only prompts live under
  ``scenarios/chat_handoff/``; integration gather / live / terminal scenarios
  stay in ``complex_shell_prompts/``.
- Preserve routing decision observability contracts used in tests:
  `fallback_reason` semantics and `matched_signals` (`cli_agent_action_plan`, etc.).

## Routing test execution requirements (locked)

- Routing tests are part of the default CI/CD flow; do **not** move them to
  optional-only jobs.
- Keep deterministic routing contracts (`test_routing_scenarios.py::test_deterministic_routing` and
  `test_routing_fixture_integrity.py`) in the default PR CI flow. These run as
  the no-LLM `routing-checks` job in `.github/workflows/routing-live.yml`
  (`pytest cli/interactive_shell/routing/tests/ -m "not live_llm"`), which
  needs no secrets and therefore also gates fork PRs.
- Run the live-LLM routing suites (`test_routing_scenarios.py` live tests) on
  **both** same-repo pull requests and post-merge `main` pushes, sharded across
  **8** shards (`ROUTING_SHARD_TOTAL=8`, matrix `shard_index: 0..7`) via the
  `routing-live` job in `.github/workflows/routing-live.yml`. Fork PRs skip the
  live job (no secrets); the `routing-checks` job still gates them.
- Execute routing suites with heavy parallelism (`pytest-xdist`, e.g. `-n auto`)
  in both local and CI environments.
- Local developer goal: the live-LLM routing contract suite should be runnable
  in roughly 20 seconds on a typical dev machine.
- If runtime drifts above the target, reduce/curate prompt cases first (keep
  coverage signal high), then tune worker parallelism; avoid weakening core
  deterministic routing coverage.
