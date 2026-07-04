# Gateway Package Guidance

Gateway tests live in `gateway/tests/`, not the repo-wide `tests/` tree.

This package is a bounded messaging surface with its own app entrypoint,
platform adapters, storage, security, sinks, and process runner. Keeping its
tests package-local makes gateway refactors easier to review and keeps the
gateway implementation and regressions together. New gateway unit tests should
be added under `gateway/tests/`.

Pytest discovers these tests through `pytest.ini`; scoped CI maps changes under
`gateway/` to `gateway/tests/` through `.github/ci/test_scope_rules.py`.

## Layout

- `manager.py` — process composition root: builds the turn handler, starts the
  Telegram worker, owns signals and shutdown.
- `turn_handler.py` — transport-agnostic turn callback:
  `build_gateway_turn_handler(console=...)` returns
  `(text, session, sink, logger) -> None` that drives
  `Agent.dispatch_message_to_headless_agent(...)`.
- `telegram_gateway.py` — wires the handler into the Telegram polling worker.
- `storage/session/resolver.py` — per-chat session binding; delegates
  create / resolve / rotate to `SessionManager`.

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
