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

Public surface: `ports.py` (the Protocol seams — output, confirmation, session
store, tool provider, prompt-context provider, telemetry, error reporter,
evidence gatherer), `agent_builder.py` (`AgentConfig` + `build_agent`, the
single instantiation site for `core.agent.Agent` across all surfaces). Turn
drivers live under `turns/`:

- `orchestrator.py` — `run_turn`: the three-path routing (summarize-observation
  / handled / gather+answer). Resolves integrations **once** at the top of the
  turn onto the frozen `turn_snapshot`; downstream code reads
  `turn_snapshot.resolved_integrations` rather than re-resolving. Do NOT
  reintroduce per-component integration resolution.
- `action_driver.py`, `evidence_driver.py` — the action and evidence-gather
  turn drivers, each building an `AgentConfig` via a `_build_*_agent` factory.
- `headless_dispatch.py` — `HeadlessAgent` for API/test/gateway turns. `tools`
  is required; pass `NullToolProvider()` explicitly for a text-only turn.

Everything else (`tools/`, `accounting/`, `prompts/`, `grounding/`, `session/`,
`error_reporting.py`, `llm_resolution.py`) is a responsibility-scoped
subpackage or helper — read the directory for details.

## Session lifecycle (owned by SessionManager)

`core.agent_harness.session.SessionManager` is the single owner of session
create / resolve / rotate / restore / flush. Every surface (shell, gateway,
headless) delegates lifecycle to it instead of re-implementing bootstrap +
persistence — gateway's `SessionResolver` delegates create/resolve/rotate to
it; headless in-memory sessions bypass it by design (never persisted, no
lifecycle to manage) but still run tool-calling turns through the shared
harness.

`Session` (formerly `ReplSession`) is the in-memory session object used by
every surface, including headless gateway — it is not REPL-specific. Do not
re-add per-surface session bootstrap logic; extend `SessionManager` instead.

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

`core/agent/` is one file per responsibility (see
[docs/NAMING.md](../../docs/NAMING.md)). `Agent` (in `agent.py`) is a thin
facade: `__init__` stores construction-time config and `run()` resolves
per-run context and hands it to `core.agent.react_loop.run_react_loop`, which
owns the actual think → call-tools → observe algorithm. `mixins.py` provides
`EventEmitterMixin` / `ToolFilterMixin` / `SteeringMixin`; `provider_hooks.py`
provides `ProviderHookDelegate`, a fail-open wrapper around
`core.provider.ProviderHooks` — a raised hook exception is logged and
swallowed, never breaks the loop.

Do not reintroduce hook-method overrides on `Agent` itself (e.g. a subclass
overriding a private `_before_provider_request`-style method) — customize via
`provider_hooks=ProviderHooks(...)` at construction instead. Subclassing
remains the pattern for `_filter_tools` and `_should_accept_conclusion`, which
are genuine per-agent overrides, not seams `ProviderHooks` covers.
