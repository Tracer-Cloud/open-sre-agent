# agent_harness/ package rules

`agent_harness/` is the **decoupled agent harness** for two agent shapes: the
tool-calling loop (`core.agent.Agent` via `build_agent`) and the direct-answer
path (`stream_answer` via the `StreamAnswerFn` seam in `ports.py`, no tools).
It was extracted out of `interactive_shell` so the same harness can run the
interactive terminal and be invoked headlessly via
`agent_harness.turns.headless_dispatch`.

## Host API (teach this)

Prefer `AgentSession.start()` → `.chat` / `.investigate` — not free-function
turn dumps. Construct one agent per logical session (or scheduled loop), then
many turns — do not rebuild every message. Process boot
(`configure_process`) and headless construction (`build_default_headless_agent`)
are separate layers.

| Path | Call |
|------|------|
| Process boot (once) | `configure_process(PROFILE)` — adapters only; not agent construction |
| Happy path | `AgentSession.start()` → repeated `.chat` / `.investigate` |
| Custom host | **`build_default_headless_agent(...)`** (only factory) → `attach_agent` once → many `.chat` |
| Gateway | `SessionAgentPool` → factory once / session → `bind_turn` → `.chat` |
| Scheduled one-shot | `AgentSession.run_headless_turn(...)` (not the multi-turn pattern) |

Do **not** duplicate the default port stack outside `build_default_headless_agent`.
Shell uses a TTY `ChatDispatcher` (same `AgentSession.chat` / `run_turn`) —
intentional, not a second headless factory. Do not reintroduce peer
`bootstrap.adapters` copies under surfaces or gateway.

**Bind ports:** session-aware defaults implement
`SessionBindable` / `ConsoleBindable` / `OutputBindable` (`ports.py`).
`HeadlessAgent.bind_session` / `bind_turn(console=…, output=…)` only call ports
that match those Protocols. Gateway usually keeps a stable `LiveOutputSink` and
rebinds the transport via `LiveOutputSink.bind` (no `output=` each turn).

**Host cancel:** one `threading.Event` on the output sink
(`ensure_turn_cancel` / `host_cancel_requested` in `turns/host_cancel.py`) —
tools (console `cancel_requested`), orchestrator/gather, and stream guards all
read that same Event. Do not invent a second cancel channel.

**Cloud scale-out:** more Fargate tasks (fleet), not unbound in-process
concurrency or a new `chat` API.

## Hard boundary

- **No `import interactive_shell` anywhere under `agent_harness/`.** The dependency
  direction is strictly one-way: `interactive_shell -> agent_harness -> core`.
- `agent_harness/` may depend on `core/`, `config/`, and `platform/`. It must not
  import `integrations/`, `tools/`, `surfaces/`, or `gateway/`. Integration and tool
  behavior reaches the harness through ports in `platform/harness_ports.py`, wired at
  startup via `install_harness_ports()` in the interactive-shell output boundary.
  It must not depend on terminal UI concerns (Rich rendering, prompt-toolkit
  mutable UI state, slash dispatch, the shell `REGISTRY`).

## Layout

Top level holds the package's public surface — `__init__.py` (the curated
re-exports), `ports.py`, `agent_builder.py` — plus two small cross-cutting default
port impls that fit no single subpackage: `error_reporting.py`
(`DefaultErrorReporter`) and `llm_resolution.py` (`default_llm_factory` /
`resolve_provider_models`). Everything else lives in a responsibility-scoped
subpackage. Default port implementations live with the concern they serve, not in a
`providers/` package.

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
  - `action_driver.py` — `ActionTurnRunner`: one action tool-calling turn
    over the ports, via a `_build_action_agent` factory that returns an
    `ActionTurnPlan`.
  - `evidence_driver.py` — bounded evidence-gather loop, via a
    `_build_evidence_agent` factory that returns an `AgentConfig` handed to
    `build_agent`.
  - `headless_dispatch.py` — headless programmatic entry point
    (`HeadlessAgent`, constructed with the ports then `.dispatch(message)` per turn)
    plus in-memory port adapters for
    API / test runs. `tools` is required — surfaces that want a text-only
    turn pass `NullToolProvider()` explicitly.
  - `turn_snapshot.py` / `turn_results.py` — the immutable per-turn `TurnSnapshot`
    (built from any object satisfying `TurnSnapshotSource`, not `Session` directly)
    and the neutral turn-result models.
  - `default_reasoning_client.py` — `DefaultReasoningClientProvider`, kept with the
    reasoning-client family (`stream_answer`, `StaticReasoningClientProvider`).
- `tools/` — action-tool wiring over the canonical registry (`action_tools.py`,
  `tool_context.py`) and `tool_provider.py` (`DefaultToolProvider`).
- `accounting/` — session-scoped token accounting and LLM run metadata, plus the
  default `TurnAccounting` (`turn_accounting.py`) and `RunRecordFactory`
  (`run_record.py`).
- `prompts/` — prompt builders by agent path (pure string assembly; grounding
  via `PromptContextProvider`). Layout: `kernel/`
  (envelope + surface Strategy), `assistant/` / `action/` / `gather/` (peer
  assemblers), `grounding/` (prompt providers), plus leaves `memory/` /
  `runtime_facts/` / `skills/`.
- `grounding/` — reusable grounding cache and rendering contracts; surfaces
  inject surface-owned command registries instead of being imported here.
- `session/` — reusable agent session state (`SessionCore`), JSONL storage, prompt
  history, task registry, session-scoped background records, integration resolution
  (:mod:`session.integration_resolution`), and `SessionManager` (the lifecycle owner).
  See "Session lifecycle" below.

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
- **gateway** — process boot is
  :func:`bootstrap.process.configure_process` (``GATEWAY_PROFILE``);
  `GatewayManager` stays lifecycle-only (credentials → process boot →
  transports). Per-chat session create/resolve stays on
  `gateway/core/storage/session/resolver.py::SessionResolver` →
  `SessionManager`. Turn dispatch uses `HeadlessAgent` via
  `gateway/core/runtime/turn_handler.py`'s `GatewayTurnHandler` with
  :class:`~core.agent_harness.tools.tool_provider.DefaultToolProvider`
  built from the **live per-chat session** each turn (same tool resolution as
  shell). There is no separate gateway-owned ``Agent`` instance.
- **headless / scheduled** — non-TTY hosts use
  :meth:`AgentSession.run_headless_turn` (or ``start`` + ``chat``).
  That is the same ``run_turn`` engine as the shell; do not reassemble
  ``BufferOutputSink`` + ``build_default_headless_agent`` in integrations.
  Ephemeral in-memory sessions (``headless_dispatch.InMemorySessionStore``)
  bypass ``SessionManager`` by design when tests need no JSONL.

`Session` (formerly `ReplSession`) is the in-memory session object used by every
surface, including headless gateway — it is not REPL-specific. Do not re-add
per-surface session bootstrap logic; extend `SessionManager` instead.

## Agent construction pattern (Pattern A — canonical)

Every surface builds its runtime `Agent` the same way: assemble surface-specific
values into an `AgentConfig` dataclass, then call `build_agent(config)`. This is
the single instantiation site — when `Agent.__init__`'s signature changes,
`agent_builder.py` is the single edit site for every harness surface.

1. Assemble surface-specific values (LLM, system prompt, tools, resolved
   integrations, iteration cap, observer).
2. Pack them into an `AgentConfig` dataclass.
3. Hand it to `build_agent(config)`.

```python
from core.agent_harness.agent_builder import AgentConfig, build_agent

config = AgentConfig(
    llm=llm_client,  # or None to fall back to get_llm(LLMRole.AGENT)
    system=system_prompt,
    tools=tuple(agent_tools),
    resolved_integrations=resolved,
    max_iterations=6,
    tool_resources={},  # optional
    tool_hooks=None,  # optional
    on_runtime_event=observer_callback,  # optional
)
agent = build_agent(config)
```

Action (`turns/action_driver.py::_build_action_agent`) and evidence
(`turns/evidence_driver.py::_build_evidence_agent`) assemble an
``AgentConfig`` and call ``build_agent``. The gateway turn path does not
construct a persistent ``core.agent.Agent`` — gateway chat reuses one
``HeadlessAgent`` per logical session via ``SessionAgentPool`` (each turn
``bind_turn`` + live ``DefaultToolProvider`` from the chat session). When
``Agent.__init__``'s signature changes,
``agent_builder.py`` is the single edit site for harness surfaces that call
``build_agent``.

## Agent context and data stores

Turn assembly starts in ``turns/orchestrator.py`` with
``TurnSnapshot.from_session``.

**Do NOT** reintroduce per-surface `Agent` subclasses that override
`build_llm` / `build_system_prompt` / `build_tools` / `resolved_integrations`
hooks. Those hooks were removed because they let each surface hide per-turn
configuration on `self`, which diverged routing across surfaces.

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
4. Add or extend shape-guard tests when you introduce a new entrypoint or
   rename a shape seam.
5. Public host API is `AgentSession.chat` / `AgentSession.investigate`.
   Adapters build `ChatTurnBindings` and call `dispatch_chat_turn` internally —
   never add a new top-level binder that calls `run_turn` directly.

**Read order for new code:** this file → `harness.py` (`AgentSession`) →
`turns/orchestrator.py` (`run_turn`) → `core/agent/agent.py` (facade + wiring)
→ `core/agent/react_loop.py` (`run_react_loop`, the tool-calling algorithm).

## Investigation agent — the tool-calling shape with a custom loop

`tools/investigation/stages/gather_evidence/agent.py::ConnectedInvestigationAgent`
composes the shared `EventEmitterMixin` and `ToolFilterMixin` mixins
(`core.agent.mixins`) instead of subclassing `Agent`, with a specialised ReAct
`run()` (seed calls, evidence collection, duplicate detection, stagnation
handling). It is still the tool-calling shape — composition, not a forked loop.

## Construct once → many turns

Prefer this shape in docs, samples, and new call sites — do **not** invent a
second top-level free function that dumps the turn stack, and do **not** rebuild
a headless agent on every message for the same logical session.

**Host API shape**

```python
from bootstrap.process import EMBEDDED_PROFILE, configure_process
from core.agent_harness import AgentSession

configure_process(EMBEDDED_PROFILE)   # adapters / investigation runner
session = AgentSession.start()        # construct once (session + default agent)
result = session.chat("…")            # turn 1
result = session.chat("…")            # turn 2 — same attached agent
report = session.investigate({…})     # Path-2 verb (separate stage machine)
```

**One agent per logical session (or scheduled loop)**

| Lifetime | Construct | Then |
|----------|-----------|------|
| Chat session (gateway) | `SessionAgentPool` keeps one `HeadlessAgent` per session id; each turn rebinds outer sink via `LiveOutputSink.bind`, then `bind_turn` (session / accounting / console / tool_hooks) | `AgentSession.chat` / `agent.dispatch` |
| Embedder / script | `AgentSession.start()` or `attach_agent(HeadlessAgent…)` once | repeated `chat` / `dispatch` |
| Scheduled loop | Prefer one agent for the loop’s lifetime when multi-turn; `run_headless_turn` is OK for true one-shot digests | do not treat one-shot as the multi-turn pattern |
| Interactive shell | TTY `ChatDispatcher` bound for the REPL lifetime (not `HeadlessAgent`) | `AgentSession.chat` per submission |

Same-session turns must not overlap on one pooled agent (gateway holds a
per-session lock). Different sessions stay concurrent under the capacity gate.

| Name | Use |
|------|-----|
| **`AgentSession`** + **`chat` / `investigate`** | **Public host API** — prefer in all new code |
| **`HeadlessAgent`** + **`dispatch`** | Non-TTY `ChatDispatcher` (ports object); gateway / embedders / tests |
| **`SessionAgentPool`** | Gateway: one headless agent per logical session across turns |
| **`build_default_headless_agent`** | Factory for the standard port stack when a surface supplies its own sink |
| **`run_headless_turn`** | One-shot convenience for scheduler digests — not the multi-turn pattern |
| **`dispatch_chat_turn`** | **Internal** seam over `run_turn` — adapters only |

There is no `dispatch_message_to_headless_agent` — that free-function dump was
replaced by `HeadlessAgent.dispatch` / `AgentSession.chat`.

**Scaling** is separate from the host API: local concurrency
(`TurnConcurrencyGate` / transport pools / `OPENSRE_SIZE_PROFILE`) and cloud
Fargate scale-out (spin more tasks; same API per task) sit *around*
`chat`/`investigate`. Construct-once-per-session is the reuse story;
process/task scale-out is deploy. Do not redesign the host API to “enable
scaling.”

## Four hosts, one AgentSession API

**Public host contract:** :class:`~core.agent_harness.harness.AgentSession`
with ``chat`` and ``investigate``. One name per concept — the former
``AgentHarness`` / ``HarnessConfig`` / ``HarnessStartupResult`` /
``dispatch_message`` aliases are deleted.

**Internal chat seam:** adapters build
:class:`~core.agent_harness.turns.chat_api.ChatTurnBindings`, then call
:func:`~core.agent_harness.turns.chat_api.dispatch_chat_turn` (thin facade
over ``run_turn``). Do **not** add new top-level chat entrypoints that call
``run_turn`` directly — adapters only.

**Internal investigate seam:** payload runner installed by
``bootstrap.adapters.install_investigation_api`` (via harness adapters).
``agent_harness`` must not import ``tools``.

| Host | Process boot | Host call |
|------|--------------|-----------|
| CLI / interactive shell | `configure_process(CLI_PROFILE)` + shell Rich adapters | `execute_shell_turn` → TTY `ChatDispatcher` → `AgentSession.chat` |
| Gateway chat | `configure_process(GATEWAY_PROFILE)` | `GatewayTurnHandler` → `SessionAgentPool` → `AgentSession.chat` |
| Standalone web | `configure_process(WEB_PROFILE)` | `AgentSession.investigate` (Path 2) |
| Scheduled digests | adapters via profile; runners via `install_scheduler_runners` | `AgentSession.run_headless_turn` → `chat` |

Do **not** force the REPL through `HeadlessAgent`. Shell is the TTY adapter of
the same engine; headless agents are for non-TTY hosts. Do **not** invent a
second public investigate entrypoint beside ``AgentSession.investigate``.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.

## core/agent package (Agent is a facade, not the algorithm owner)

`core/agent/` is a package with one file per responsibility. `Agent`
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
  functional entry). The conclusion phase delegates to `ConclusionHandler` in
  `core/agent/handle_conclusion.py` (textual-tool-call bounce, host acceptance,
  queued follow-ups, nudges).
- `core/agent/agent.py` — the `Agent` facade: `__init__` (holds config), `run()`
  (builds the per-run `AgentRunInput` via `_build_run_input` and hands it to
  `run_react_loop`), and the `_should_accept_conclusion` override hook.

Do not reintroduce hook-method overrides on `Agent` itself (e.g. a subclass
overriding a private `_before_provider_request`-style method) — customize via
`provider_hooks=ProviderHooks(...)` at construction instead. Subclassing
remains the pattern for `_filter_tools` and `_should_accept_conclusion`, which
are genuine per-agent overrides, not seams `ProviderHooks` covers.
