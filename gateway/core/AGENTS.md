# gateway/core/ — process and leaf infrastructure

Gateway core machinery used by every surface. **Must not** import
`gateway.transports.*` or `gateway.web` (surfaces). Channel composition lives
in `gateway.startup`; only `runtime/controller.py` imports that module.
Pinned by `gateway/tests/test_package_borders.py`.

| Package | Role |
|---------|------|
| `host/` | The host layer: builds the agent, binds the turn, runs it (`turn_handler`, `session_agents`, `live_sink`, `cancel_console`, capacity) |
| `runtime/` | Composition root (`controller`), credential hydration, security audit |
| `process/` | Daemon, polling thread, readiness |
| `middleware/` | Per-turn steps every transport runs (inbound decision, identity policy, approvals, attention, locks) |
| `storage/` | Session bindings + investigation, event and feedback stores |
| `billing/` | Credits client |
| `attachments/` | Attachment helpers |
| `session/` | Gateway chat-context helpers |
| `config/` | Logging / gateway config helpers |

Transports and `web/` may import these packages. Peer chat packages never land
here.

## Who may drive the agent

`host/` is the only package that calls harness **behaviour** — building ports,
binding a turn, running or flushing a session, formatting goal progress.
Everywhere else in `gateway/` may import harness **contracts**
(`SessionCore`, `OutputSink`, `SlashPortsFactory`, `SessionGoal`) to type a
parameter, and nothing more: a transport that runs a turn itself has become a
second turn handler.

Pinned by `gateway/tests/test_harness_behaviour_border.py`, whose allowlist can
only shrink. `web/` is on it today because `POST /investigate` embeds the agent
directly and therefore gets none of the host layer's guarantees (agent reuse,
approvals hooks, cancel console, capability policy).

## Process boot vs lifecycle

Shared process setup (env → Sentry → harness adapters → capability warnings →
LLM preload) lives in
:func:`bootstrap.process.configure_process` with ``GATEWAY_PROFILE``.
`GatewayController.start_gateway` is lifecycle-only after logging + credential
hydrate: configure process, compose **one** `GatewayTurnHandler(gate=…)`, then
`start_channels()` (delegates to :func:`gateway.channels.start_channels`) and
`start_scheduler()` (peer of the channels). Do not wrap the turn handler in a
second handler class. Do not reintroduce a bootstrap essay in the controller.
Scheduler runners register when the scheduler stage starts
(:func:`bootstrap.adapters.install_scheduler_runners`).

Process boot has one entrypoint: :func:`bootstrap.process.configure_process`
with ``GATEWAY_PROFILE``. Do not add a gateway-local wrapper around it.
