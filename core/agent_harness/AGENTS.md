# agent_harness/ package rules

`agent_harness/` is the **decoupled agent harness** for two agent shapes: the
tool-calling loop (`core.agent.Agent` via `build_agent`) and the direct-answer
path (`stream_answer` via the `StreamAnswerFn` seam in `ports.py`, no tools).
It was extracted out of `interactive_shell` so the same harness can run the
interactive terminal and be invoked headlessly via
`agent_harness.turns.headless_dispatch`.

## Hard boundary (enforced by tests)

- **No `import interactive_shell` anywhere under `agent_harness/`.** This is the whole
  point of the package and is checked by
  `tests/core/agent/test_import_boundaries.py`. The dependency direction is strictly
  one-way: `interactive_shell -> agent_harness -> core`.
- `agent_harness/` may depend on `core/`, `config/`, and `platform/`. It must not
  import `integrations/`, `tools/`, `surfaces/`, or `gateway/`. Integration and tool
  behavior reaches the harness through ports in `platform/harness_ports.py`, wired at
  startup via `install_harness_ports()` in `surfaces/interactive_shell/ui/output/boundary.py`.
  It must not depend on terminal UI concerns (Rich rendering, prompt-toolkit
  mutable UI state, slash dispatch, the shell `REGISTRY`).

## Layout

Top level holds the package's public surface: `__init__.py` (curated
re-exports), `ports.py`, `agent_builder.py`, plus small shared helpers
(`error_reporting.py`, `llm_resolution.py`). Everything else lives in a
responsibility-scoped subpackage.

- `ports.py` — Protocols the engine talks to (output, confirmation, session
  store, tool provider, prompt-context provider, telemetry, error reporter,
  evidence gatherer). Kept top-level as the central seam imported everywhere.
- `agent_builder.py` — `AgentConfig` dataclass + `build_agent(config)`. The
  single instantiation site for `core.agent.Agent` across all surfaces
  (action, evidence, gateway). See "Agent construction pattern" below.
- `turns/` — the turn drivers that orchestrate `core.agent.Agent`:
  - `orchestrator.py` — `run_turn`: the three-path routing
    (summarize-observation / handled / gather+answer). Resolves integrations
    **once** at the top of the turn onto the frozen `turn_snapshot`, so
    `turn_snapshot.resolved_integrations` is the single source of truth for
    what the turn knows. Downstream components (e.g.
    `action_driver._resolved_integrations_for_turn`) read it from there rather
    than re-resolving. Do NOT reintroduce per-component integration resolution.
  - `action_driver.py` — `run_action_agent_turn`: one action tool-calling turn
    over the ports, via a `_build_action_agent` factory that returns an
    `ActionTurnPlan`.
  - `evidence_driver.py` — bounded evidence-gather loop, via a
    `_build_evidence_agent` factory that returns an `AgentConfig` handed to
    `build_agent`.
  - `headless_dispatch.py` — headless programmatic entry point
    (`HeadlessAgent`, constructed with the ports then `.dispatch(message)` per
    turn) plus in-memory port adapters for API/test runs. `tools` is required
    — surfaces that want a text-only turn pass `NullToolProvider()` explicitly.
  - `default_reasoning_client.py` — production
    `ReasoningClientProvider` default (lazy `LLMRole.REASONING` client).
  - `turn_snapshot.py`, `turn_results.py` — neutral, surface-agnostic turn
    data shapes (immutable snapshot + facts-only result models).
- `tools/` — action-tool wiring over the canonical registry (`action_tools.py`,
  `tool_context.py`, `tool_provider.py` for `ports.ToolProvider`).
- `accounting/` — session-scoped token accounting, LLM run metadata, and
  `ports.TurnAccounting` / `ports.RunRecordFactory` defaults.
- `prompts/` — action-agent and conversational-assistant prompt builders (pure
  string assembly; grounding text is supplied via `PromptContextProvider`).
  `prompt_context.py` implements the default `PromptContextProvider`;
  `conversation_memory.py` (recent-conversation rendering shared by prompts)
  lives here too.
- `grounding/` — reusable grounding cache and rendering contracts; surfaces
  inject surface-owned command registries instead of being imported here.
- `session/` — reusable agent session state (`SessionCore`), JSONL storage,
  prompt history, task registry, session-scoped background records,
  integration resolution (`session.integration_resolution`), and
  `SessionManager` (the lifecycle owner — see "Session lifecycle" below).

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
- **gateway** — `gateway/runtime/manager.py` bootstraps the process via
  :meth:`SessionManager.create` (``open_storage=False``).
  `gateway/storage/session/resolver.py::SessionResolver` owns per-chat
  chat-id ↔ session-id binding + metadata; it delegates `create` / `resolve` /
  `rotate` to `SessionManager`. Turn dispatch uses `HeadlessAgent` via
  `gateway/runtime/turn_handler.py`'s `GatewayTurnHandler` with
  :class:`~core.agent_harness.tools.tool_provider.DefaultToolProvider`
  built from the **live per-chat session** each turn (same tool resolution as
  shell). There is no separate gateway-owned ``Agent`` instance.
- **headless** — ephemeral in-memory sessions (``headless_dispatch.InMemorySessionStore``)
  bypass ``SessionManager`` by design: they never persist to JSONL and do not
  need create/resolve/rotate/close. Tool-calling turns still run through the
  shared harness; only session lifecycle is skipped.

`Session` (formerly `ReplSession`) is the in-memory session object used by every
surface, including headless gateway — it is not REPL-specific. Do not re-add
per-surface session bootstrap logic; extend `SessionManager` instead.

## Agent construction pattern (Pattern A — canonical)

Every surface builds its runtime `Agent` the same way: assemble surface-specific
values into an `AgentConfig` dataclass, then call `build_agent(config)`. This is
the single instantiation site — when `Agent.__init__`'s signature changes,
`agent_builder.py` is the single edit site for every harness surface.

**Do NOT** reintroduce per-surface `Agent` subclasses that override `build_llm`
/ `build_system_prompt` / `build_tools` / `resolved_integrations` hooks —
they were removed because they let each surface hide per-turn configuration on
`self`, which diverged routing across surfaces.

## Two agent shapes (not one pattern with an exception)

- **Tool-calling agent** — `core.agent.Agent`, the ReAct loop (think → call
  tools → observe) driven by `llm.invoke`. Built via `AgentConfig` +
  `build_agent`. Used by the action, evidence/gather, and investigation agents.
- **Direct answer (no tools)** — `orchestrator.stream_answer`, one grounded
  text answer streamed via `client.invoke_stream` (the `StreamAnswerFn` seam).
  It does **not** use `Agent`: no tool loop, no observe step.

A new agent is one shape or the other: if it calls tools it is the tool-calling
shape; if it answers directly without tools it is the direct-answer shape.

### Contributor checklist (agent changes)

1. State the shape explicitly (tool-calling vs. direct answer) in the entrypoint
   docstring (three lines max).
2. Update this file when harness rules change.
3. Inject through `ports.py` callables (`StreamAnswerFn`, `ExecuteActions`,
   `EvidenceGatherer`); do not import surface code into `agent_harness/`.
4. Add or extend guards in `tests/core/agent_harness/test_agent_shapes.py` when
   you introduce a new entrypoint or rename a shape seam.

**Read order for new code:** this file → `turns/orchestrator.py` (`run_turn`) →
`core/agent/agent.py` (facade + wiring) → `core/agent/react_loop.py`
(`run_react_loop`, the tool-calling algorithm).

## Investigation agent — the tool-calling shape with a custom loop

`tools/investigation/stages/gather_evidence/agent.py::ConnectedInvestigationAgent`
composes the shared `EventEmitterMixin` and `ToolFilterMixin` mixins
(`core.agent.mixins`) instead of subclassing `Agent`, with a specialised ReAct
`run()` (seed calls, evidence collection, duplicate detection, stagnation
handling). It is still the tool-calling shape — composition, not a forked loop.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.

## core/agent package (Agent is a facade, not the algorithm owner)

`core/agent/` is a package with one file per responsibility (see
[docs/NAMING.md](../../docs/NAMING.md) for the naming convention). `Agent`
(in `agent.py`) is a thin facade: `__init__` stores construction-time config
and `run()` resolves per-run context (from `runtime_request=` or
`initial_messages=`) and hands it to `core.agent.react_loop.run_react_loop`,
which owns the actual think → call-tools → observe algorithm.

- `core/agent/mixins.py` — `EventEmitterMixin` (event dispatch),
  `ToolFilterMixin` (tool-narrowing hook), `SteeringMixin` (`steer`/`follow_up`
  to nudge a run in progress). `Agent` composes all three;
  `ConnectedInvestigationAgent` composes the first two instead of subclassing
  `Agent` (see "Investigation agent" above).
- `core/agent/provider_hooks.py` — `ProviderHookDelegate`, a fail-open wrapper
  around `core.provider.ProviderHooks` applied around each LLM call. A raised
  hook exception is logged and swallowed; it never breaks the loop.
- `core/agent/loop_host.py` — `LoopHost`, the `Protocol` `run_react_loop` calls
  back into. `Agent` implements it via the mixins plus its own
  `_transform_messages` / `_convert_to_llm` / `_before_request` /
  `_after_response` forwarders. The concrete `ProviderHookDelegate` type is an
  `Agent` implementation detail, not part of the host contract, so any host can
  wire those four provider hooks however it likes.
- `core/agent/run_io.py` — `AgentRunInput` (resolved per-run inputs) and
  `AgentRunResult` (the loop's outcome). `core.agent` re-exports `AgentRunResult`
  for the `from core.agent import AgentRunResult` path.
- `core/agent/react_loop.py` — `ReactLoop` (the loop as a method-object, phases
  `_think` / `_handle_conclusion` / `_observe`) and `run_react_loop` (its thin
  functional entry).
- `core/agent/agent.py` — the `Agent` facade: `__init__` (holds config), `run()`
  (builds the per-run `AgentRunInput` via `_build_run_input` and hands it to
  `run_react_loop`), and the `_should_accept_conclusion` override hook.

Do not reintroduce hook-method overrides on `Agent` itself (e.g. a subclass
overriding a private `_before_provider_request`-style method) — customize via
`provider_hooks=ProviderHooks(...)` at construction instead. Subclassing
remains the pattern for `_filter_tools` and `_should_accept_conclusion`, which
are genuine per-agent overrides, not seams `ProviderHooks` covers.
