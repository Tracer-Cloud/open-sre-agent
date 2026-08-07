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
| Composition root / process | `core/runtime/manager.py` |
| Daemon pidfile / status | `core/runtime/daemon.py` |
| Turn callback | `core/runtime/turn_handler.py` |
| Sink + callback contracts | `core/runtime/sink_protocol.py` |
| Config error type | `core/runtime/errors.py` (`GatewayConfigurationError`) |
| Web surface (FastAPI) | `web/webapp.py` (`app`) |
| Telegram start | `transports/telegram/wiring.py` (`start_telegram_worker`) |
| Slack start | `transports/slack/wiring.py` (`start_slack_worker`) |
| Discord start | `transports/discord/wiring.py` (`start_discord_worker`) |

## Layout

Packages are split like `core/agent_harness/prompts/`: **core infra** vs
**peer surfaces**. See `gateway/core/AGENTS.md` and
`gateway/transports/AGENTS.md`.

- `core/` — process and leaf infrastructure (`runtime`, `storage`,
  `billing`, `attachments`, `session`, `config`). No imports from transports
  or `web`, except the composition root `core/runtime/manager.py`, which
  wires them together.
- `transports/` — chat peers (`slack`, `discord`, `telegram`). Each owns
  settings, inbound worker, security, output sink, and `wiring.py`. Peers
  never import each other; anything two need belongs in `core/` (usually
  `gateway.core.runtime`). See `transports/AGENTS.md`.
- `web/` — web surface (FastAPI app, investigations API, worker/artifacts).
  May import `core/`; must not import chat transports. See `web/AGENTS.md`.
- `core/storage/session/resolver.py` — per-conversation session binding
  keyed by platform; delegates create / resolve / rotate to `SessionManager`.

### Dependency rule (acyclic)

```
core  ←  transports.{slack,discord,telegram} · web
         (peer surfaces — never import each other)
```

Sole exception: `core/runtime/manager.py` (composition root) imports the
transports and `web`. Package DAG pinned by
`tests/test_package_borders.py` (plus discord↔slack isolation in
`tests/discord/test_transport_borders.py`).

Tests stay flat under `gateway/tests/{runtime,web,slack,discord,telegram,…}/`
(nesting `tests/transports/discord` collides with the `discord` PyPI package
name during collection).

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
