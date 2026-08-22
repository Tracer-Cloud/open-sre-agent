# Infrastructure package

These instructions apply to `infrastructure/` and all of its subdirectories.
The repo-root `AGENTS.md` still applies.

## Purpose

`infrastructure/` owns shared runtime services that sit **outside** the
user-facing application surfaces and **outside** the core agent harness loop.

It may own side effects: telemetry, audit logging, tracing, auth, masking,
sandbox execution, and minimal guardrails.  
Configuration belongs in `config/`.  
Agent orchestration, state, tool planning, and execution contracts belong in
`core/`.

Name packages by **what they do**. Do not create `common/`, `shared/`, or
`util/` drawers. Prefer leaf imports over re-export shims.

## Package map and ownership

See the full ownership map in [`README.md`](./README.md).  
The three deliberately root-level modules are:

| Module | Owns | Keep out |
| --- | --- | --- |
| `alert_intake.py` | Minimal HTTP surface (`POST /alerts`) that depends only on the alert domain model. Both the gateway web app and the interactive shell can host it without importing each other. | Gateway-specific or REPL-specific logic |
| `asgi_server.py` | Generic transport: run any ASGI app in a background thread (`serve_asgi_in_thread`). One app, one port per host process; `port=0` binds an ephemeral port. | Any particular web surface or application logic |
| `setup_state.py` | Install/setup facts surfaced to agents through prompt context. | Runtime configuration or agent loop state |

When a change crosses package boundaries, extract a small helper into the
owning area rather than adding logic to the caller.

## Cross-cutting rules

- Prefer leaf imports. Avoid new re-export shims.
- Keep import-time work light — no threads, network, or heavy I/O at module
  import time.
- Root-level modules (`alert_intake.py`, `asgi_server.py`, `setup_state.py`)
  stay root deliberately so multiple surfaces can share them without circular
  imports.
- Future migrations into this package must leave one canonical import path;
  do not leave compatibility-only forwarding modules.
