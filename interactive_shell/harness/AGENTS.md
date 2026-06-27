# Harness package rules

These instructions apply to `interactive_shell/harness/` and all
subdirectories. Parent `AGENTS.md` files still apply.

## Readability and helper-function policy

## Compatibility-shim policy

- Do **not** keep compatibility-only forwarding modules (files that only
  re-export symbols from a new location) in `harness/`.
- After all local imports/tests are migrated, remove the shim in the same
  change rather than leaving it behind.
- Prefer one canonical import path per harness concern; avoid dual old/new
  module paths that add maintenance noise.

- Do **not** introduce tiny wrapper helpers that only forward a call, rename a
  variable, or return a trivial tuple/value.
- As a hard rule, avoid creating helper functions that are ~1-3 lines unless
  they satisfy one of the allowed exceptions below.
- Prefer keeping short, linear logic inline in the main turn-handling flow when the
  helper would force readers to jump around the file.

### Allowed exceptions

Small helpers are allowed only when at least one is true:

- **Boundary isolation:** wraps exception handling, I/O, or cross-module
  boundary behavior that should be isolated for safety.
- **Reuse:** used in multiple call sites (not just one).
- **Domain naming:** captures an action-planning concept that is non-obvious inline.
- **Test seam:** creates a stable seam needed for deterministic tests.

### Review checklist

Before adding any helper in harness modules, ask:

1. Does this reduce cognitive load, or just split obvious logic?
2. Would inline code be clearer than jumping to another function?
3. Is there a concrete exception from the allowed list above?

If all answers are weak, keep the logic inline.

## Test Placement Policy For Harness

- Harness tests must be co-located under `interactive_shell/harness/tests/`.
- Do **not** move turn tests back under `tests/interactive_shell/harness/`.
- When adding a new harness-phase test file, place it under
  `interactive_shell/harness/tests/` and keep turn test fixtures
  under that subtree.

## Harness Test Ownership And File Layout

| File | Ownership | Scope |
| --- | --- | --- |
| `interactive_shell/harness/tests/test_turn_scenarios.py` | Harness package owners | Canonical runner: deterministic command detection, live action planning, turn-execution oracles |
| `interactive_shell/harness/tests/test_turn_fixture_integrity.py` | Harness package owners | Scenario-tree/schema/no-mocks guardrails |
| `interactive_shell/harness/tests/scenario_loader.py` | Harness package owners | Load `scenarios/<behavior_class>/<id>/{scenario.yml,answer.yml}` |
| `interactive_shell/harness/tests/scenarios/**/scenario.yml` | Harness package owners | Input world: prompt, session, capabilities, intent metadata |
| `interactive_shell/harness/tests/scenarios/**/answer.yml` | Harness package owners | Expected behavior: turn, policy, planned/executed actions, response contract |

## Scenario schema and `available_capabilities` semantics

Scenario fixtures are intentionally minimal: the loader (`scenario_loader.py`)
parses only fields the runner asserts on. Do **not** re-add decorative metadata.

- **Removed fields (do not re-add):** `risk_level`, `tier`,
  `session.remote_connected`, and `input.surface` were parsed-but-unused and have
  been dropped from the schema and every fixture. `title` and `notes` remain as
  human-only documentation.
- **`available_capabilities` is a three-state, production-faithful knob.** It
  constrains which shell action tools the live oracle offers. Its default mirrors
  production: `ReplSession()` carries no capability constraints, so every tool is
  enabled. For each surface (`slash_commands`, `cli_commands`, `synthetic_suites`):
  - **omit the key (or omit the whole block)** → the tool stays enabled (the
    production default). This is the right choice for almost every scenario.
  - **explicit empty list `[]`** → the tool is explicitly disabled (hidden from
    the action-agent tool set and blocked at execution). Use only when a scenario genuinely
    needs that surface off to test a specific path.
  - **non-empty list** → an allowlist: the tool is enabled and the action
    normalizer drops proposed actions outside the list.
- **Do not re-introduce all-empty `available_capabilities` blocks.** A block that
  disables all three surfaces is the old redundant boilerplate that predated the
  production-faithful default; it is rejected by
  `test_available_capabilities_blocks_are_not_redundant_boilerplate`. Omit the
  block instead.

## Harness Test Isolation Policy

- Do **not** use `unittest.mock`, `patch`, `MagicMock`, or equivalent mocking
  primitives in turn tests.
- Do **not** stub or monkeypatch the LLM client path in turn tests.
- Harness contract tests must exercise the real turn-execution stack
  (`InteractiveShellController._run_queued_turn` / initial-input replay ->
  `handle_message_with_agent`) and rely on curated
  prompts instead of synthetic mocked return values.

## Important Turn-Handling Decisions (locked)

- **Employment-impacting rule:** do not implement regex, keyword, fuzzy,
  literal-slash, or "obvious intent" shortcuts that bypass the action agent.
  Engineers have been fired before for adding exactly this kind of shortcut.
  Treat this as a hard architectural boundary, not a style preference.
- There is no top-level classifier: every turn is handed to
  `handle_message_with_agent`. Do **not** add command/slash/help/alert branches
  or any other top-level phases before the agent.
- There is no pre-agent command dispatcher in `handle_message_with_agent`.
  Every turn enters the agent pipeline; slash command execution belongs to the
  action-agent tool path (`slash_invoke`) rather than a pre-agent dispatcher or
  deterministic action shortcut.
- The runtime (`runtime/utils/input_policy.py`) may reuse
  `orchestration.command_dispatch.deterministic_command_text` for terminal-UI
  concerns only (spinner suppression and exclusive-stdin gating). It must not
  grow into natural-language intent inference or an action-execution shortcut.
- Regex fallback has been intentionally removed. Do **not**
  re-introduce legacy regex fallback phases
  unless there is an explicit product decision to restore them. A product
  decision still requires changing this AGENTS guidance in the same PR; do not
  sneak a regex or slash short-circuit in as a "small fix."
- **The shell action agent is the sole tool selector for non-command turns.**
  There is no regex/keyword intent inference and no deterministic
  natural-language → action mapping in `orchestration/`. The former
  `slash_commands/deterministic_action_mapper.py`, `intent_parser` regex
  patterns/extractors, and the regex planner postprocessing overrides were
  removed. Do **not** reintroduce them: change tool selection by editing the
  action-agent system prompt (`orchestration/action_system_prompt.py`) and the per-tool
  descriptions in `orchestration/tools/*`, never by adding pattern matching.
  Tool-call argument *validation* belongs to the first-class AgentTool runtime
  contract and tool availability gates; intent *classification* by regex is not.
- When the action-agent prompt overflows, hand off to the conversational
  `assistant` rather than guessing an action. When the action-agent LLM itself
  is unavailable, render and persist a failed assistant turn so `/resume` shows
  the outage.
- **No planning-stage fail-closed safeguard (v0.1).** There is no "I couldn't
  safely decide actions" denial. Every terminal action is read-only, so an
  unmatched, ambiguous, or chatty clause never blocks a turn: the action agent runs
  the clauses it can map and the rest fall through to the assistant. The former
  `denied` decision, the `mark_unhandled` planner tool, the `UNHANDLED:` text
  convention, and `render_plan_denied` were removed for this reason. Do **not**
  reintroduce a planning-stage denial; if write/mutating actions are ever added,
  gate them with an execution-stage confirmation (see
  `orchestration/execution_policy.py`), not a planner denial. The legacy
  `fail_closed`, `has_unhandled_clause`, and `turn.expected_signals` fixture
  fields were removed (the oracle never asserted on them); the policy block now
  carries a single `executes_terminal_action` boolean. See the `Answer` and
  `AnswerPolicy` docstrings in `tests/scenario_loader.py` for the two execution
  paths a turn can take (action-agent tools vs conversational tool-gathering) and
  which fixture fields cover which.
- Scenario fixtures use a single ``tool_actions`` list (not separate
  ``executed_actions`` / ``gathered_tools_contract`` blocks). Each entry has
  ``surface: dispatch`` (terminal action shape: slash, investigation, …) or
  ``surface: gather`` with ``expect`` (``not_called``, ``called``, ``call_any``,
  ``valid_data``, ``valid_data_any``). Handoff-only prompts live under
  ``scenarios/chat_handoff/``; integration gather / live / terminal scenarios
  stay in ``complex_shell_prompts/``.
- Preserve shell action observability contracts used in tests and analytics.

## Turn test execution requirements (locked)

- Turn tests are part of the default CI/CD flow; do **not** move them to
  optional-only jobs.
- Keep deterministic dispatch contracts
  (`test_turn_scenarios.py::test_deterministic_command_text_matches_scenario` and
  `test_turn_fixture_integrity.py`) in the default PR CI flow. These run as
  the no-LLM `turn-checks` job in `.github/workflows/interactive-shell-live.yml`
  (`pytest interactive_shell/harness/tests/ -m "not live_llm"`), which
  needs no secrets and therefore also gates fork PRs.
- Run the live-LLM turn suites (`test_turn_scenarios.py` live tests) on
  **both** same-repo pull requests and post-merge `main` pushes, sharded across
  **8** shards (`TURN_SHARD_TOTAL=8`, matrix `shard_index: 0..7`) via the
  `turn-live` job in `.github/workflows/interactive-shell-live.yml`. Fork PRs skip the
  live job (no secrets); the `turn-checks` job still gates them.
- Execute turn suites with heavy parallelism (`pytest-xdist`, e.g. `-n auto`)
  in both local and CI environments.
- Local developer goal: the live-LLM turn contract suite should be runnable
  in roughly 20 seconds on a typical dev machine.
- If runtime drifts above the target, reduce/curate prompt cases first (keep
  coverage signal high), then tune worker parallelism; avoid weakening core
  deterministic command-detection coverage.
