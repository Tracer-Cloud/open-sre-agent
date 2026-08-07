# gateway/core/ — process and leaf infrastructure

Gateway core machinery used by every surface. **Must not** import
`gateway.transports.*` or `gateway.web` (surfaces). Channel composition lives
in `gateway.channels`; only `runtime/manager.py` imports that module.
Pinned by `gateway/tests/test_package_borders.py`.

| Package | Role |
|---------|------|
| `runtime/` | Composition root (`manager`), turn handler, approvals, attention, daemon |
| `storage/` | Session bindings + investigation stores |
| `billing/` | Credits client |
| `attachments/` | Attachment helpers |
| `session/` | Gateway chat-context helpers |
| `config/` | Logging / gateway config helpers |

Transports and `web/` may import these packages. Peer chat packages never land
here.

## Process boot vs lifecycle

Shared process setup (env → Sentry → harness adapters → capability warnings →
LLM preload) lives in
:func:`bootstrap.process.configure_process` with ``GATEWAY_PROFILE``.
`GatewayManager.start_gateway` is lifecycle-only after logging + credential
hydrate: configure process, compose **one** `GatewayTurnHandler(gate=…)`, then
`start_channels()` (delegates to :func:`gateway.channels.start_channels`) and
`start_scheduler()` (peer of the channels). Do not wrap the turn handler in a
second handler class. Do not reintroduce a bootstrap essay in the manager.
Scheduler runners register when the scheduler stage starts
(:func:`bootstrap.adapters.install_scheduler_runners`).

Process boot has one entrypoint: :func:`bootstrap.process.configure_process`
with ``GATEWAY_PROFILE``. Do not add a gateway-local wrapper around it.
