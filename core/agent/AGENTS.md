# agent/ package rules

`agent/` owns the **decoupled agentic turn engine**: the surface-agnostic
think -> call-tools -> observe loop and the turn harness (action tool-calling
turn, three-path routing, conversational answer, evidence gather), extracted out
of `interactive_shell` so the same engine can run the interactive terminal and
be invoked headlessly via `agent.api`.

## Hard boundary (enforced by tests)

- **No `import interactive_shell` anywhere under `agent/`.** This is the whole
  point of the package and is checked by
  `tests/agent/test_import_boundary.py`. The dependency direction is strictly
  one-way: `interactive_shell -> agent -> core.runtime`.
- `agent/` may depend on `core/`, `config/`, `platform/`, `integrations/`, and
  `tools/`. It must not depend on terminal/REPL concerns (Rich, prompt-toolkit,
  `ReplSession`, slash dispatch, the shell `REGISTRY`). Those are reached through
  the Protocols in `agent/ports.py`, which `interactive_shell` implements as
  adapters.

## Layout

- `ports.py` — Protocols the engine talks to (output, confirmation, session
  store, tool provider, prompt-context provider, action dispatch, telemetry,
  error reporter, evidence gatherer).
- `context.py` — `TurnContext`, the immutable per-turn snapshot (built from any
  object satisfying `TurnContextSource`, not `ReplSession` directly).
- `conversation_history.py` — recent-conversation rendering shared by prompts.
- `prompts/` — action-agent and conversational-assistant prompt builders (pure
  string assembly; grounding text is supplied via `PromptContextProvider`).
- `results.py` — neutral turn-result models.
- `driver.py` — `run_agent_turn`: one action tool-calling turn over the ports,
  wrapping `core.runtime.agent.Agent`.
- `engine.py` — `run_turn`: the three-path routing (summarize-observation /
  handled / gather+answer) and the conversational answer.
- `gather.py` — bounded evidence-gather loop over the `core` investigation tools.
- `headless/` — minimal in-memory port adapters for API / test execution.
- `api.py` — the headless programmatic entry point.

## Keep the loop primitive in core

The ReAct loop primitive is `core.runtime.agent.Agent`. `agent/` orchestrates it;
it does not re-implement it. Do not fork the loop here.
