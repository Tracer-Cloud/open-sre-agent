# Gateway Package Guidance

Gateway tests live in `gateway/tests/`, not the repo-wide `tests/` tree — add
new gateway unit tests there. `pytest.ini` discovers them and
`.github/ci/test_scope_rules.py` scopes CI to that path when only `gateway/`
changes.

## Entry points (open these first)

| Role | Path |
|------|------|
| Package main | `main.py` (`python -m gateway.main`) |
| Production entry (slash ports) | `surfaces.cli.gateway_entry` (`python -m surfaces.cli.gateway_entry`) |
| Composition root / process | `runtime/manager.py` |
| Daemon pidfile / status | `runtime/daemon.py` |
| Turn callback | `runtime/turn_handler.py` |
| Sink + callback contracts | `runtime/sink_protocol.py` |
| Shared config error | `runtime/errors.py` (`GatewayConfigurationError`) |
| HTTP FastAPI app | `http/webapp.py` (`app`) |
| Telegram start | `telegram/wiring.py` (`start_telegram_worker`) |
| Slack start | `slack/wiring.py` (`start_slack_worker`) |

## Layout

- `runtime/` — process and turn machinery. `runtime/manager.py` is the
  composition root: builds the turn handler, starts the transport workers,
  owns signals and shutdown. `runtime/turn_handler.py` is the
  transport-agnostic turn callback: `GatewayTurnHandler` (a
  `(text, session, sink, logger) -> None` callable) builds a fresh
  `HeadlessAgent` per turn and calls `agent.dispatch(text)`.
  `runtime/sink_protocol.py` holds `GatewaySink` + `GatewayAgentCallback`;
  `runtime/errors.py` holds `GatewayConfigurationError`.
- `http/` — everything served over HTTP: `http/webapp.py` (FastAPI app),
  `http/web_server.py`, the `/api/investigations` routes, and the
  investigation store / worker / artifacts.
- `telegram/` and `slack/` — one package per transport, each owning settings,
  the inbound worker, inbound security, the output sink, and `wiring.py`
  (e.g. `telegram/wiring.py` wires the handler into the polling worker).
- `storage/session/resolver.py` — per-conversation session binding keyed by
  platform; delegates create / resolve / rotate to `SessionManager`.

Tests mirror the subpackages: `gateway/tests/{runtime,http,telegram,slack}/`.

## Gateway turn dispatch

- **No persistent gateway `Agent` instance.** Each inbound message gets a
  per-chat `Session` from `SessionResolver` and is handled by the shared
  headless dispatch path (`core.agent_harness.turns.headless_dispatch`).
- The turn handler callback signature is exactly four arguments: `text`,
  `session`, `sink`, and `logger`. Do not reintroduce `chat_id` into this
  contract; the sink owns chat transport details.
- Resolve action tools from the live per-chat `Session` each turn via
  `DefaultToolProvider(session, console)` — same as the interactive shell.
  Do **not** precompute tools at process start; chat sessions carry their own
  integration context after `SessionResolver.resolve`.
- Per-chat session lifecycle (create / resolve / rotate / restore) is owned by
  `SessionResolver` → `SessionManager`, not by `GatewayManager`.

## Testing

Gateway E2E regression tests should drive a normalized polled Telegram message
into `handle_polled_inbound_telegram_message(...)` and let it invoke the turn
handler. Do not test this path by swapping in fake LLM clients when validating
dispatch wiring; prefer explicit registered commands such as `/status` when the
test only needs to validate providers and callback plumbing.
