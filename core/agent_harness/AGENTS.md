# agent_harness/ package rules

`agent_harness/` owns the **decoupled agent harness** around the shared
`core.agent.Agent` loop: action tool-calling turns, three-path routing,
conversational answers, evidence gather, and headless execution. It was
extracted out of `interactive_shell` so the same harness can run the interactive
terminal and be invoked headlessly via `agent_harness.agents.headless_agent`.

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

Top level holds only the package's public surface: `__init__.py` (the curated
re-exports), `ports.py`, and `agent_builder.py`. Everything else lives in a
responsibility-scoped subpackage.

- `ports.py` — Protocols the engine talks to (output, confirmation, session
  store, tool provider, prompt-context provider, telemetry, error reporter,
  evidence gatherer). Kept top-level as the central seam imported everywhere.
- `agent_builder.py` — `AgentConfig` dataclass + `build_agent(config)`. The
  single instantiation site for `core.agent.Agent` across all surfaces
  (action, evidence, gateway). See "Agent construction pattern" below.
- `agents/` — the turn drivers that orchestrate `core.agent.Agent`:
  - `action_agent.py` — `run_action_agent_turn`: one action tool-calling turn
    over the ports. Uses `_build_action_agent` factory that returns an
    `AgentConfig` handed to `build_agent`.
  - `turn_orchestrator.py` — `run_turn`: the three-path routing
    (summarize-observation / handled / gather+answer) and the conversational
    answer.
  - `evidence_agent.py` — bounded evidence-gather loop. Uses
    `_build_evidence_agent` factory that returns an `AgentConfig` handed to
    `build_agent`.
  - `headless_agent.py` — headless programmatic entry point
    (`dispatch_message_to_headless_agent`) plus in-memory port adapters for
    API / test runs. `tools` is required — surfaces that want a text-only
    turn pass `NullToolProvider()` explicitly.
- `models/` — neutral, surface-agnostic data shapes:
  - `turn_context.py` — `TurnContext`, the immutable per-turn snapshot (built from any
    object satisfying `TurnContextSource`, not `ReplSession` directly).
  - `turn_results.py` — neutral turn-result models.
- `providers/` — core-owned default port implementations and provider resolution
  (`default_providers.py`, `default_prompt_context.py`, `provider_models.py`).
- `tools/` — action-tool wiring over the canonical registry (`action_tools.py`,
  `tool_context.py`).
- `accounting/` — session-scoped token accounting and LLM run metadata.
- `prompts/` — action-agent and conversational-assistant prompt builders (pure
  string assembly; grounding text is supplied via `PromptContextProvider`).
  `conversation_memory.py` (recent-conversation rendering shared by prompts) lives here.
- `grounding/` — reusable grounding cache and rendering contracts; surfaces
  inject surface-owned command registries instead of being imported here.
- `session/` — reusable agent session state, JSONL storage, prompt history,
  task registry, and session-scoped background records.
- `integrations/` — integration resolution helpers for the harness.

## Agent construction pattern (Pattern A — canonical)

Every surface builds its runtime `Agent` the same way:

1. Assemble surface-specific values (LLM, system prompt, tools, resolved
   integrations, iteration cap, observer).
2. Pack them into an `AgentConfig` dataclass.
3. Hand it to `build_agent(config)`.

```python
from core.agent_harness.agent_builder import AgentConfig, build_agent

config = AgentConfig(
    llm=llm_client,                    # or None to fall back to get_agent_llm()
    system=system_prompt,
    tools=agent_tools,
    resolved_integrations=resolved,
    max_iterations=6,
    tool_resources={},                  # optional
    tool_hooks=None,                    # optional
    on_runtime_event=observer_callback, # optional
)
agent = build_agent(config)
```

Gateway (`gateway/start_gateway.py::build_gateway_agent`), action
(`agents/action_agent.py::_build_action_agent`), and evidence
(`agents/evidence_agent.py::_build_evidence_agent`) all follow this shape.
When `Agent.__init__`'s signature changes, `agent_builder.py` is the single
edit site — every surface adopts the change automatically.

**Do NOT** reintroduce per-surface `Agent` subclasses that override
`build_llm` / `build_system_prompt` / `build_tools` / `resolved_integrations`
hooks. Those hooks were removed for this reason: they let each surface hide
per-turn configuration on `self`, which fragmented context loading and
diverged the four surfaces (see issue #3347 and Vincent's 2026-07-01
braindump).

## Answer agent — categorical exception

`turn_orchestrator.answer_cli_agent` streams grounded text via
`client.invoke_stream(prompt)` and does **not** use `core.agent.Agent`. Reason:
`Agent.run()` is a tool-calling loop; streaming a no-tool text response is a
different modality. If streaming is added to `Agent` in a future change,
`answer_cli_agent` should be migrated onto it. Until then, treat this as an
intentional gap, not a hook-pattern regression.

## Investigation agent — extends Agent, custom run()

`tools/investigation/stages/gather_evidence/agent.py::ConnectedInvestigationAgent`
extends `Agent[RegisteredTool]` to reuse the shared event-emission and
`_filter_tools` infrastructure, then overrides `.run()` with a specialised
ReAct loop (seed calls, evidence collection, duplicate detection, stagnation
handling). This is a legitimate use of subclassing — the specialised loop
cannot delegate to `Agent.run()`'s generic tool-calling loop. The class does
**not** override the removed config hooks; it assembles its config inline at
the top of `run()`.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.
