# Gateway Package Guidance

Gateway tests live in `gateway/tests/`, not the repo-wide `tests/` tree — add
new gateway unit tests there. `pytest.ini` discovers them and
`.github/ci/test_scope_rules.py` scopes CI to that path when only `gateway/`
changes.

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
**peer surfaces** vs **composer**. See `gateway/core/AGENTS.md`,
`gateway/channels/AGENTS.md`, and `gateway/transports/AGENTS.md`.

- `core/` — process and leaf infrastructure (`runtime`, `storage`,
  `billing`, `attachments`, `session`, `config`). No imports from transports
  or `web`. Only `core/runtime/manager.py` imports `gateway.channels`.
- `channels/` — starts/stops web + chat transports as one consumer set.
  Imports `web/` and each peer's `startup` only. See `channels/AGENTS.md`.
- `transports/` — chat peers (`slack`, `discord`, `telegram`). Each owns
  settings, inbound worker, security, output sink, and `startup.py`. Peers
  never import each other or `channels`/`web`; anything two need belongs in
  `core/` (usually `gateway.core.runtime`). See `transports/AGENTS.md`.
- `web/` — web surface (FastAPI app, investigations API, worker/artifacts).
  May import `core/`; must not import chat transports or `channels`. See
  `web/AGENTS.md`.
- `core/storage/session/resolver.py` — per-conversation session binding
  keyed by platform; delegates create / resolve / rotate to `SessionManager`.

### Dependency rule (acyclic)

```
core.manager  →  channels  →  transports.{telegram,slack,discord}.startup
                           →  web
peer transports · web  →  core leaves
(peers never import each other, channels, or each other's packages)
```

Package DAG pinned by `tests/test_package_borders.py` (plus discord↔slack
isolation in `tests/discord/test_transport_borders.py`).

Tests stay flat under `gateway/tests/{runtime,web,slack,discord,telegram,…}/`
(nesting `tests/transports/discord` collides with the `discord` PyPI package
name during collection).

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
`SessionResolver` may adopt a same-document legacy empty-id Telegram row into
the scoped key on first scoped resolve.

## Capacity (process gate vs transport pools)

Two different limits — do not conflate them:

| Layer | Mechanism | Behavior when full |
|-------|-----------|-------------------|
| **Process** | `TurnConcurrencyGate` from `OPENSRE_SIZE_PROFILE` (SMALL=1, MEDIUM=2, LARGE=4) | Chat turns: non-blocking `try_acquire` on `GatewayTurnHandler`; reply with busy message and drop the turn. Scheduler runners: **blocking** `acquire` (already-claimed work waits for a slot). |
| **Per-transport** | `max_concurrent_turns` (defaults to the same profile limit via `turn_limit_for_profile`; override with `*_GATEWAY_MAX_CONCURRENT`) | Caps how many inbound messages that transport may process in parallel *before* they hit the shared turn handler. Does not replace the process gate. |

```text
Telegram/Slack/Discord ──► GatewayTurnHandler.try_acquire ──► TurnConcurrencyGate
Scheduler (agent + investigate runners) ──► blocking acquire ──► same gate
POST /investigate + InvestigationWorker ──► AgentSession.investigate (Path-2) ──► ungated today
```

- Production chat capacity is on `GatewayTurnHandler(gate=manager.turn_gate)`.
- `ConcurrencyLimitedTurnHandler` is **quarantined under**
  `gateway/tests/runtime/concurrency_limited_handler.py` (tests only). Do not
  reintroduce it under `gateway/core/` — production uses `gate=` on
  `GatewayTurnHandler` only.
- **Chat vs investigate:** the process gate covers gateway **chat** and
  **scheduler** runners. Path-2 HTTP investigate (`POST /investigate`,
  `InvestigationWorker`) does **not** take the gate today — do not assume web
  investigations are capped by `OPENSRE_SIZE_PROFILE`. Analytics: chat uses
  `gateway_turn_*` with `surface` ∈ {slack,telegram,discord}; investigate uses
  separate `investigation_*` events (no capacity-reject event yet).

## Host parity (channels)

Same turn engine for Slack / Telegram / Discord: ingress → `GatewayTurnHandler`
→ `SessionAgentPool` → `AgentSession.chat`. Web investigate is Path-2 (separate
verb) — see Capacity above. Values: **yes** / **partial** / **no** / **n/a**.

| Concern | Slack | Telegram | Discord | Web |
|---------|-------|----------|---------|-----|
| Cancel / stop mid-turn | **partial** — soft timeout UX; handler not cancelled | **no** — no turn timeout / user-stop | **partial** — same as Slack | **partial** — queued investigate cancel only |
| Approvals / `before_tool_call` | **yes** — Block Kit + `approval_tool_hooks` | **yes** — inline keyboard + `approval_tool_hooks` | **yes** — components + `approval_tool_hooks` | **n/a** — Path-2 |
| Tool resolution | **yes** — live `DefaultToolProvider(session)` | **yes** — same | **yes** — same | **n/a** — investigate runner |
| Sink redaction | **yes** — `user_facing_error_message` | **yes** — same | **yes** — same | **yes** — `type(exc).__name__` only |
| Principal / actor | **yes** — `slack/principal.py` | **yes** — `telegram/principal.py` | **yes** — `discord/principal.py` | **partial** — Clerk org audit; no `StorageScope` |
| Capacity gate | **yes** — process gate + transport pool | **yes** — same + TG semaphore | **yes** — same + executor | **no** — Path-2 ungated |

**Documented exceptions (do not “fix” by forking a second loop):**

- Gateway chat disables `task_cancel` / investigation / llm_provider
  (`GatewayTurnHandler._UNSUPPORTED_GATEWAY_CAPABILITIES`).
- Path-2 web investigate is ungated and has no chat approval prompter.
- True mid-turn cancel is still missing on all chat hosts (soft timeout ≠ cancel).
- Telegram write-tool approvals require a non-empty `allowed_user_ids` allowlist
  (same fail-closed posture as Discord).

**Characterization:** live-session tools —
`tests/runtime/test_turn_handler.py` +
`test_gateway_chat_never_builds_core_agent_or_precomputed_tools`;
redaction — Slack/Telegram/Discord sink tests + `tests/runtime/test_status_messages.py`;
Telegram approvals — `tests/telegram/test_approvals.py`.

**Dogfood + smoke (turn-engine regressions):**

| Check | How |
|-------|-----|
| Borders + capacity | `pytest gateway/tests/test_package_borders.py gateway/tests/runtime/test_concurrency_gate.py -q` |
| Smoke gateway wiring | `./trace smoke --suite gateway` (or tags covering `cli.gateway_*`) |
| Dogfood (dev silo only) | `@mention` on **dev** Slack — thread continuity, Digging in…, `Want me to:` → `yes`; one Socket Mode consumer. See notes `ops-dogfood.html#glossary`. |
| Not a substitute | Laptop `opensre gateway` + smoke ≠ dogfood |

## Testing

Gateway E2E regression tests should drive a normalized polled Telegram message
into `handle_polled_inbound_telegram_message(...)` and let it invoke the turn
handler. Do not test this path by swapping in fake LLM clients when validating
dispatch wiring; prefer explicit registered commands such as `/status` when the
test only needs to validate providers and callback plumbing.
