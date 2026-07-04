# agent_harness/ package rules

`agent_harness/` owns the **decoupled agent harness** for two agent shapes:
the tool-calling loop (`core.agent.Agent` via `build_agent`) and the
direct-answer path (`stream_answer` via the `StreamAnswerFn` seam in
`ports.py`, no tools). It orchestrates action tool-calling turns, three-path routing,
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
    object satisfying `TurnContextSource`, not `Session` directly).
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
- `session/` — reusable agent session state (`Session`), JSONL storage, prompt
  history, task registry, session-scoped background records, and
  `SessionManager` (the lifecycle owner). See "Session lifecycle" below.
- `integrations/` — integration resolution helpers for the harness.

## Session lifecycle (owned by SessionManager)

`core.agent_harness.session.SessionManager` is the single owner of session
create / resolve / rotate / restore / flush. Every surface delegates lifecycle
to it instead of re-implementing bootstrap + persistence:

- **shell** — `SessionBootstrapSpec` calls `SessionManager().bootstrap(...)` for
  the core startup mutations (persistent task registry + integration
  hydration), then layers shell-only UI concerns (theme, grounding providers,
  prompt history) on top. Interactive REPL entry calls
  :meth:`SessionManager.open_storage` once the run is confirmed interactive;
  ``/new`` calls :meth:`SessionManager.rotate_in_place`; ``/resume`` calls
  :meth:`SessionManager.rebind_for_resume` then :meth:`SessionManager.restore_context`.
  REPL exit calls :meth:`SessionManager.close` via
  :meth:`SessionManager.for_session`.
- **gateway** — `gateway/manager.py` bootstraps the process via
  :meth:`SessionManager.create` (``open_storage=False``).
  `gateway/storage/session/resolver.py::SessionResolver` owns per-chat
  chat-id ↔ session-id binding + metadata; it delegates `create` / `resolve` /
  `rotate` to `SessionManager`. Turn dispatch uses
  `Agent.dispatch_message_to_headless_agent` via `gateway/turn_handler.py` with
  :class:`~core.agent_harness.providers.default_providers.DefaultToolProvider`
  built from the **live per-chat session** each turn (same tool resolution as
  shell). There is no separate gateway-owned ``Agent`` instance.
- **headless** — ephemeral in-memory sessions (``headless_agent.InMemorySessionStore``)
  bypass ``SessionManager`` by design: they never persist to JSONL and do not
  need create/resolve/rotate/close. Tool-calling turns still run through the
  shared harness; only session lifecycle is skipped.

`Session` (formerly `ReplSession`) is the in-memory session object used by every
surface, including headless gateway — it is not REPL-specific. Do not re-add
per-surface session bootstrap logic; extend `SessionManager` instead.

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
    tools=tuple(agent_tools),
    resolved_integrations=resolved,
    max_iterations=6,
    tool_resources={},                  # optional
    tool_hooks=None,                    # optional
    on_runtime_event=observer_callback, # optional
)
agent = build_agent(config)
```

Action (`agents/action_agent.py::_build_action_agent`) and evidence
(`agents/evidence_agent.py::_build_evidence_agent`) assemble an
``AgentConfig`` and call ``build_agent``. The gateway turn path does not
construct a persistent ``Agent`` — it uses
``Agent.dispatch_message_to_headless_agent`` with per-turn
:class:`~core.agent_harness.providers.default_providers.DefaultToolProvider`
from the live chat session. When ``Agent.__init__``'s signature changes,
``agent_builder.py`` is the single edit site for harness surfaces that call
``build_agent``.

## Agent context and data stores

See `docs/agent-context-data-stores.md`. Turn assembly starts in
``agents/turn_orchestrator.py`` with ``TurnContext.from_session``.

**Do NOT** reintroduce per-surface `Agent` subclasses that override
`build_llm` / `build_system_prompt` / `build_tools` / `resolved_integrations`
hooks. Those hooks were removed because they let each surface hide per-turn
configuration on `self`, which diverged routing across surfaces.

## Two agent shapes (not one pattern with an exception)

The harness has **two** intentional agent shapes. This is a design, not a 4/4
uniformity claim with an exception bolted on:

- **Tool-calling agent** — `core.agent.Agent`, the ReAct loop (think → call
  tools → observe) driven by `llm.invoke`. Built via `AgentConfig` +
  `build_agent` (the construction pattern above). Used by the action,
  evidence/gather, and investigation agents.
- **Direct answer (no tools)** — `turn_orchestrator.stream_answer`, one grounded
  text answer streamed via `client.invoke_stream` (the `StreamAnswerFn` seam in
  `ports.py`). It does **not** use `Agent`: there is no tool loop and no observe
  step, and it streams on a different client method.

A new agent is one shape or the other: if it calls tools it is the tool-calling
shape; if it answers directly without tools it is the direct-answer shape.

### Contributor checklist (agent changes)

Before opening or merging an agent PR, confirm:

1. **Shape** — State explicitly: tool-calling (`Agent` / `build_agent` /
   `ExecuteActions`) or direct answer (`StreamAnswerFn` / `invoke_stream`, no tools).
2. **Entrypoint docstring** — The public function or class documents which shape
   it implements (three lines max; link here if helpful).
3. **Docs** — Update this file when harness rules change; update
   `docs/agent-context-data-stores.md` when routing or prompt capture changes
   (diagram must match runtime — assistant never flows through `Agent.run()`).
4. **Seams** — Inject through `ports.py` callables (`StreamAnswerFn`,
   `ExecuteActions`, `EvidenceGatherer`); do not import surface code into
   `agent_harness/`.
5. **Tests** — Add or extend guards in
   `tests/core/agent_harness/test_agent_shapes.py` when you introduce a new
   entrypoint or rename a shape seam.

**Read order for new code:** this file → `docs/agent-context-data-stores.md` →
`agents/turn_orchestrator.py` (`run_turn`) → `core/agent.py` (facade + wiring)
→ `core/agent_loop.py` (`run_react_loop`, the tool-calling algorithm).

## Investigation agent — the tool-calling shape with a custom loop

`tools/investigation/stages/gather_evidence/agent.py::ConnectedInvestigationAgent`
composes the shared `AgentEventEmitter` and `AgentToolFilter` mixins
(`core.agent_mixins`) instead of subclassing `Agent`, and owns a specialised
ReAct `run()` (seed calls, evidence collection, duplicate detection, stagnation
handling). It is still the tool-calling shape — a specialised loop that reuses
the two agent hooks by composition rather than delegating to the generic
`Agent.run()`. It assembles its config inline at the top of `run()`.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.

## core/agent* module split (Agent is a facade, not the algorithm owner)

`core/agent.py` is a thin facade + hook binder — `Agent.__init__` stores
construction-time config and `Agent.run()` resolves per-run context (from
`agent_context=` or `initial_messages=`) and hands it to
`core.agent_loop.run_react_loop`, which owns the actual think → call-tools →
observe algorithm:

- `core/agent_mixins.py` — `AgentEventEmitter` (event dispatch),
  `AgentToolFilter` (tool-narrowing hook), `AgentSteering` (the
  `steer`/`follow_up` stop/continue/redirect control-plane). `Agent` composes
  all three; `ConnectedInvestigationAgent`
  (`tools/investigation/stages/gather_evidence/agent.py`) composes the first
  two directly instead of subclassing `Agent`.
- `core/agent_hooks.py` — `AgentProviderHookDelegate`, a fail-open wrapper
  around `core.provider.ProviderHooks` (context transform, provider
  request/response, LLM conversion). A raised hook exception is logged and
  swallowed; it never breaks the loop.
- `core/agent_loop.py` — `AgentRunContext` (resolved per-run inputs),
  `AgentLoopHost` (the `Protocol` `run_react_loop` calls back into — `Agent`
  implements it via the mixins above plus its own `_transform_context` /
  `_convert_to_llm` / `_before_request` / `_after_response` forwarders), and
  `run_react_loop` itself. `AgentLoopHost` intentionally exposes the four
  provider-hook seams as method calls, not a `_hooks: AgentProviderHookDelegate`
  attribute — the concrete delegate type is an `Agent` implementation detail,
  not part of the host contract, so any host can wire the seams however it
  likes. `AgentRunResult` also lives here; `core.agent` re-exports it for the
  existing `from core.agent import AgentRunResult` import path.
- `core/agent.py` — the facade: static `dispatch_message_to_headless_agent` /
  `resolve_integrations` entrypoints, `__init__`, `run()`, and the
  `_should_accept_conclusion` override hook.

Do not reintroduce hook-method overrides on `Agent` itself (e.g. a subclass
overriding a private `_before_provider_request`-style method) — customize via
`provider_hooks=ProviderHooks(...)` at construction instead, which
`AgentProviderHookDelegate` applies. Subclassing remains the pattern for
`_filter_tools` and `_should_accept_conclusion`, which are genuine per-agent
overrides, not seams `ProviderHooks` covers.
