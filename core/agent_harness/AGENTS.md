# agent_harness/ package rules

`agent_harness/` owns the **decoupled agent harness** around the shared
`core.agent.Agent` loop: action tool-calling turns, three-path routing,
conversational answers, evidence gather, and headless execution. It was
extracted out of `interactive_shell` so the same harness can run the interactive
terminal and be invoked headlessly via `agent_harness.headless_agent`.

## Hard boundary (enforced by tests)

- **No `import interactive_shell` anywhere under `agent_harness/`.** This is the whole
  point of the package and is checked by
  `tests/core/agent/test_import_boundaries.py`. The dependency direction is strictly
  one-way: `interactive_shell -> agent_harness -> core`.
- `agent_harness/` may depend on `core/`, `config/`, `platform/`, `integrations/`, and
  `tools/`. It must not depend on terminal UI concerns (Rich rendering,
  prompt-toolkit mutable UI state, slash dispatch, the shell `REGISTRY`). The
  reusable session model, prompt history, grounding cache contracts, and task
  records live here; `interactive_shell` supplies adapters and registry
  providers at runtime.

## Layout

- `ports.py` — Protocols the engine talks to (output, confirmation, session
  store, tool provider, prompt-context provider, telemetry, error reporter,
  evidence gatherer).
- `turn_context.py` — `TurnContext`, the immutable per-turn snapshot (built from any
  object satisfying `TurnContextSource`, not `ReplSession` directly).
- `conversation_memory.py` — recent-conversation rendering shared by prompts.
- `prompts/` — action-agent and conversational-assistant prompt builders (pure
  string assembly; grounding text is supplied via `PromptContextProvider`).
- `grounding/` — reusable grounding cache and rendering contracts; surfaces
  inject surface-owned command registries instead of being imported here.
- `session/` — reusable agent session state, JSONL storage, prompt history,
  task registry, and session-scoped background records.
- `turn_results.py` — neutral turn-result models.
- `action_agent.py` — `run_agent_turn`: one action tool-calling turn over the ports,
  wrapping `core.agent.Agent`.
- `turn_orchestrator.py` — `run_turn`: the three-path routing (summarize-observation /
  handled / gather+answer) and the conversational answer.
- `evidence_agent.py` — bounded evidence-gather loop over the `core` investigation tools.
- `headless/` — minimal in-memory port adapters for API / test execution.
- `headless_agent.py` — the headless programmatic entry point.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.
