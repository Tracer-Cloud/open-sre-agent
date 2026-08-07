# Gateway Package Guidance

Gateway unit tests live under this package’s `tests/` tree, not the repo-wide
tests tree.

## Entry points (open these first)

| Role | Path |
|------|------|
| Production entry (slash ports) | CLI: `opensre gateway start` / `--foreground` (composition root outside this package) |
| Package main | `main.py` — **fails closed** (no slash-port glue; not a production entry) |
| Composition root / process | `core/runtime/manager.py` (`GatewayManager`; inject `slash_ports_factory`) |
| Channels (web + chat composer) | `channels/` (`start_channels` / `ChannelsHandle`) |
| Daemon pidfile / status | `core/runtime/daemon.py` |
| Turn callback | `core/runtime/turn_handler.py` |
| Sink + callback contracts | `core/runtime/sink_protocol.py` |
| Config / transport errors | `core/runtime/errors.py` (`GatewayConfigurationError`, `GatewayTransportFailedError`) |
| Web surface (FastAPI) | `web/webapp.py` (`app`) |
| Chat registry (inside channels) | `channels/chat.py` (`start_transports` / `stop_transports`) |
| Telegram start | `transports/telegram/startup.py` (`start_telegram_worker`) |
| Slack start | `transports/slack/startup.py` (`start_slack_worker`) |
| Discord start | `transports/discord/startup.py` (`start_discord_worker`) |

Bare `python -m gateway.main` and `manager.main()` / `start_gateway()` without
`slash_ports_factory` exit with a clear error. Unit tests may construct
`GatewayManager(...)` directly (factory optional there).

## Lifecycle (`GatewayManager`)

```
start_gateway()
  → configure_process(GATEWAY_PROFILE)
  → compose turn handler
  → start_channels()   # delegates to gateway.channels (web + chat)
  → start_scheduler()  # peer of channels — not a "channel"
  → ready
```

- **Channels** live in `gateway/channels/` — sole composer of web + Telegram /
  Slack / Discord. Manager keeps only `ChannelsHandle`.
- Missing chat credentials → `not configured`; readiness/runtime failures →
  `failed`. The rest still start.
- **Scheduler** starts after channels and is a peer (cron / loops), not a
  transport. Daemon pidfile/status stays in `core/runtime/daemon.py` — do not
  fold the process daemon into a "scheduler" package.
- `gateway.core` must not import `gateway.transports` / `gateway.web`; only
  `manager.py` imports `gateway.channels`.
- Peer transports and `web` must not import `gateway.channels`.

## Layout

Packages are split like `core/agent_harness/prompts/`: **core infra** vs
**peer surfaces** vs **composer**.

- `core/` — process and leaf infrastructure (`runtime`, `storage`,
  `billing`, `attachments`, `session`, `config`). No imports from transports
  or `web`. Only `core/runtime/manager.py` imports `gateway.channels`.
- `channels/` — starts/stops web + chat transports as one consumer set.
  Imports `web/` and each peer's `startup` only.
- `transports/` — chat peers (`slack`, `discord`, `telegram`). Each owns
  settings, inbound worker, security, output sink, and `startup.py`. Peers
  never import each other or `channels`/`web`; anything two need belongs in
  `core/` (usually `gateway.core.runtime`).
- `web/` — web surface (FastAPI app, investigations API, worker/artifacts).
  May import `core/`; must not import chat transports or `channels`.
- `core/storage/session/resolver.py` — per-conversation session binding
  keyed by platform; delegates create / resolve / rotate to `SessionManager`.

### Dependency rule (acyclic)

```
core.manager  →  channels  →  transports.{telegram,slack,discord}.startup
                           →  web
peer transports · web  →  core leaves
(peers never import each other, channels, or each other's packages)
```

Package DAG and peer isolation are pinned by border tests. Keep gateway tests
flat by surface (do not nest a directory named after the Discord PyPI package).

## Gateway turn dispatch

- **One turn handler:** `GatewayTurnHandler` (optional `gate=` for capacity).
  Transport Slack/Discord/Telegram *dispatchers* are ingress only — authorize,
  resolve session, build sink, then call the shared callback. Do not add a
  second production turn-handler class next to `GatewayTurnHandler`.
- **Logging** is configured once at the gateway process level
  (`configure_logging` in `GatewayManager.start_gateway`) — that is intentional.
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

## Tenancy (principal / actor)

Slack, Discord, and Telegram each resolve a `StorageScope` in their own
`transports/<peer>/principal.py` (peer isolation — no cross-imports):

- **Principal** = silo `ORGANIZATION_ID` (fail closed if missing)
- **Actor** = platform user id
- Turn runs under `bound_storage_scope` so bindings/sessions/integrations land
  on the org mount or `~/.opensre/orgs/<id>/`

CLI / interactive shell stay unbound (legacy empty principal/actor ids).
`SessionResolver` may adopt a same-document legacy empty-id row into the scoped
key once (`adopt_unscoped_binding` removes the unscoped row) so a second actor
cannot inherit that session.

## Capacity (process gate vs transport pools)

**Cloud scale-out** (“infinite” via new Fargate tasks) is a **third** layer
above these two: each task is one gateway process with its own gate; raise
fleet size / workers when saturated — do not unbound the in-process gate or
redesign `AgentSession.chat`.

Two different **in-process** limits — do not conflate them:

| Layer | Mechanism | Behavior when full |
|-------|-----------|-------------------|
| **Process** | `TurnConcurrencyGate` / `process_turn_gate()` from `OPENSRE_SIZE_PROFILE` (SMALL=1, MEDIUM=2, LARGE=4) | Chat + sync `/investigate`: non-blocking `try_acquire` (busy drop / 503). Scheduler + `InvestigationWorker`: **blocking** `acquire` (already-claimed work waits). |
| **Per-transport** | `max_concurrent_turns` (defaults to the same profile limit via `turn_limit_for_profile`; override with `*_GATEWAY_MAX_CONCURRENT`) | Caps how many inbound messages that transport may process in parallel *before* they hit the shared turn handler. Does not replace the process gate. |

```text
Telegram/Slack/Discord ──► GatewayTurnHandler.try_acquire ──► process_turn_gate()
Scheduler (agent + investigate runners) ──► blocking acquire ──► same gate
POST /investigate ──► try_acquire (busy → 503) ──► same gate
InvestigationWorker ──► blocking acquire (already claimed) ──► same gate
```

- Production chat capacity is on `GatewayTurnHandler(gate=manager.turn_gate)`.
- `GatewayManager` and Path-2 share :func:`~gateway.core.runtime.concurrency.process_turn_gate`.
- `ConcurrencyLimitedTurnHandler` is tests-only. Do not reintroduce it under
  `gateway/core/` — production uses `gate=` on `GatewayTurnHandler` only.
- **Chat + Path-2:** HTTP `/investigate` busy-drops like chat; the investigation
  worker blocks like scheduler runners. Analytics: chat uses `gateway_turn_*`
  with `surface` ∈ {slack,telegram,discord}; investigate uses separate
  `investigation_*` events (no dedicated capacity-reject event yet).

## Agent lifetime

Construct **one** `HeadlessAgent` per logical chat session
(`SessionAgentPool`), then many turns. Each inbound message:

1. `LiveOutputSink.bind(outer_gateway_sink)` — stable live sink on the agent;
   outer transport sink changes per turn.
2. `bind_turn(session=…, accounting=…, console=…, tool_hooks=…)` — session /
   cancel / approvals. Do **not** pass `output=` here unless replacing the
   `OutputSink` object itself (then `OutputBindable` ports, e.g. reasoning,
   must follow).
3. `AgentSession.chat` → `dispatch`.

Do **not** build a fresh headless agent on every message. Same-session turns
serialize on the pool’s per-session lock; different sessions stay concurrent
under the capacity gate. Multi-turn scheduled loops should keep one agent for
the loop; true one-shot digests may use `AgentSession.run_headless_turn`.

## Host parity (channels)

Same turn engine for Slack / Telegram / Discord: ingress → `GatewayTurnHandler`
→ `SessionAgentPool` → `AgentSession.chat`. Web investigate is Path-2 (separate
verb) — see Capacity above. Values: **yes** / **partial** / **no** / **n/a**.

| Concern | Slack | Telegram | Discord | Web |
|---------|-------|----------|---------|-----|
| Cancel / stop mid-turn | **yes** — soft timeout + user `/stop` via `ActiveTurnCancels` → `sink.turn_cancel` | **yes** — same | **yes** — same | **partial** — queued investigate cancel only |
| Approvals / `before_tool_call` | **yes** — Block Kit + `approval_tool_hooks` | **yes** — inline keyboard + `approval_tool_hooks` | **yes** — components + `approval_tool_hooks` | **n/a** — Path-2 |
| Tool resolution | **yes** — live `DefaultToolProvider(session)` | **yes** — same | **yes** — same | **n/a** — investigate runner |
| Sink redaction | **yes** — `user_facing_error_message` | **yes** — same | **yes** — same | **yes** — `type(exc).__name__` only |
| Principal / actor | **yes** — `slack/principal.py` | **yes** — `telegram/principal.py` | **yes** — `discord/principal.py` | **partial** — Clerk org audit; no `StorageScope` |
| Capacity gate | **yes** — process gate + transport pool | **yes** — same + TG semaphore | **yes** — same + executor | **yes** — same `process_turn_gate` (HTTP try_acquire / worker blocking) |

**Documented exceptions (do not “fix” by forking a second loop):**

- Gateway chat disables `task_cancel` / investigation / llm_provider
  (`gateway.core.runtime.capability_policy.ensure_gateway_capability_policy`).
- Path-2 web investigate shares the process gate but has no chat approval prompter.
- Soft turn timeout **and** user `/stop` / `stop` / `/cancel` set
  `sink.turn_cancel` so the ReAct loop / remaining tools stop cooperatively
  (shell `cancel_requested` parity via `CancelConsole` + `ActiveTurnCancels`).
  Orchestrator skips gather/answer and reports `final_intent=cli_agent_cancelled`;
  the live sink stops draining stream chunks when the Event fires. `/stop` is
  handled **outside** the per-conversation turn lock so it can interrupt an
  in-flight turn. The executor thread is not killed; in-flight LLM/provider
  calls still finish the current request.
- Telegram write-tool approvals require a non-empty `allowed_user_ids` allowlist
  (same fail-closed posture as Discord).

**Characterization:** cover live-session tools, sink redaction, Telegram
approvals, and soft timeout in the gateway test suite.

**Dogfood + smoke (turn-engine regressions):**

| Check | How |
|-------|-----|
| Borders + capacity | Gateway border + concurrency gate tests |
| Smoke gateway wiring | Local smoke suite for gateway / `cli.gateway_*` tags |
| Dogfood (dev silo only) | `@mention` on **dev** Slack — thread continuity, Digging in…, `Want me to:` → `yes`; one Socket Mode consumer. |
| Not a substitute | Laptop `opensre gateway` + smoke ≠ dogfood |

## Testing

Gateway E2E regression tests should drive a normalized polled Telegram message
into `handle_polled_inbound_telegram_message(...)` and let it invoke the turn
handler. Do not test this path by swapping in fake LLM clients when validating
dispatch wiring; prefer explicit registered commands such as `/status` when the
test only needs to validate providers and callback plumbing.
