# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms: Telegram DM text chat
via long polling, Slack mentions/DMs via Socket Mode, and Discord via Gateway WebSocket.

The gateway is a separate surface. Transports receive an injected turn handler;
agent startup and integration loading run through the shared harness, not
transport-specific code.

## Entry points

| What you want | File / symbol | How it is started |
|---------------|---------------|-------------------|
| **Production entry** | CLI composition root (outside `gateway/`) | `opensre gateway start` / `--foreground` (wires slash ports) |
| **Package main** | `gateway/main.py` → `main()` | Fails closed — no slash-port glue |
| **Composition root (impl)** | `gateway/core/runtime/manager.py` → `GatewayManager` | Injected `slash_ports_factory` from CLI; bare `manager.main` fails closed |
| **Background daemon helpers** | `gateway/core/runtime/daemon.py` | Used by CLI `gateway start/stop/status` (pidfile + `components.json`) |
| **Web surface (web-only task)** | `gateway/web/webapp.py` → `app` | `uvicorn gateway.web.webapp:app` (`MODE=web` in Docker) |
| **Channels** | `gateway/channels/` → `start_channels` / `ChannelsHandle` | Called by `GatewayManager.start_channels` |
| **Chat transport registry** | `gateway/channels/chat.py` → `start_transports` | Used by `gateway.channels.compose` |
| **Telegram transport** | `gateway/transports/telegram/startup.py` → `start_telegram_worker` | Via channels chat registry |
| **Slack transport** | `gateway/transports/slack/startup.py` → `start_slack_worker` | Via channels chat registry |
| **Discord transport** | `gateway/transports/discord/startup.py` → `start_discord_worker` | Via channels (includes readiness wait) |
| **Per-message turn** | `gateway/core/runtime/turn_handler.py` → `GatewayTurnHandler` | Injected into chat transports as the agent callback |

```text
opensre gateway start
        │
        ▼
gateway.core.runtime.daemon.start_gateway_daemon
        │  spawns surface-owned argv (see surfaces.shared.gateway_entrypoint):
        │    venv:   python -m surfaces.cli.gateway_entry
        │    frozen: opensre gateway start --foreground
        ▼
surfaces/cli/gateway_entry.py  (or Click foreground → same composition root)
        │  wires headless slash ports
        ▼
gateway.core.runtime.manager.GatewayManager.start_gateway
        ├── start_channels()  →  gateway.channels.start_channels
        │     ├── web/web_server  →  web/webapp:app
        │     └── channels/chat.start_transports
        │           (telegram / slack / discord startup)
        └── start_scheduler()   # peer of channels, not a transport
```

Layout: `core/` (runtime, storage, …), `channels/` (consumer composer),
`transports/` (slack, discord, telegram peers), and `web/` (web surface). See
`AGENTS.md`.

## How the pieces fit (surfaces, gateway, integrations)

Three things that are easy to mix up:

- **Surface** — a way a person talks *to* the agent (message in, answer out). Today
  there are three: the interactive shell (`surfaces/interactive_shell`, you type in
  a terminal), the CLI one-shot (`surfaces/cli`, one command → one answer), and the
  **gateway** (`gateway/`, you chat with the agent from a chat app).
- **Gateway** — one specific surface: the always-on process that connects a chat app
  to the agent. It speaks **Telegram** (long poll), **Slack** (Socket Mode), and **Discord** (Gateway WebSocket).
- **Integrations + tools** — the *outbound* / teammate side: the agent reading and
  posting in Slack. Shared client: `integrations/slack/web_client.py`. Tools:
  `slack_send_message` (webhook), `slack_reply_message` (bot token, any channel),
  `slack_read_messages` (history / thread), `slack_list_team_members` (roster).
  See `docs/messaging/slack.mdx` for OAuth scopes.

Both platforms are symmetric:

| | Inbound (person → agent) | Outbound / teammate tools |
|---|---|---|
| **Telegram** | Yes — `gateway/transports/telegram/` | Yes — integration + tool |
| **Slack** | Yes — `gateway/transports/slack/` (Socket Mode; each thread is a conversation) | Yes — webhook + bot-token tools |
| **Discord** | Yes — `gateway/transports/discord/` (DMs, mentions, threads; `/investigate`) | Yes — bot-token delivery + slash registration |

**One core for every surface.** Shell, CLI, and the gateway transports all hand the
message to the same place: a `HeadlessAgent` (`agent.dispatch(message)`). They differ
only in *how they receive input and send output* — never in how the agent thinks.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Allow your Slack member id (profile → Copy member ID; see below)
uv run opensre messaging allow -p slack -u U0123ABCD

# Start the gateway daemon (web app + Telegram chat + Slack chat + task scheduler)
uv run opensre gateway start
```

**Find your Slack user id (member ID):**

1. Open your profile in the Slack app (avatar / name).
2. Click **⋯** (More) next to **View as** / profile actions.
3. Choose **Copy member ID** — that value starts with `U…` and is what
   `SLACK_ALLOWED_USERS` / `messaging allow -p slack -u …` need.
4. Do **not** use `@display-name` (e.g. `@Yauhen`); only the member ID is stable.

Both transports load configuration the same way: tokens from env first with the
integration store as fallback; allowed users from the integration store
(written by `opensre messaging allow`) first with the `*_ALLOWED_USERS` env
var as fallback.

DM your bot from Telegram, or mention/DM it in Slack (see
`docs/messaging/slack.mdx` for the Slack app setup).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (default 4) |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | Slack app-level token for Socket Mode (`xapp-…`) |
| `SLACK_ALLOWED_USERS` | Comma-separated Slack user ids (required unless open workspace) |
| `SLACK_ALLOW_OPEN_WORKSPACE` | `1` allows any workspace member (dogfood only) |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_ALLOWED_USERS` | Comma-separated Discord user snowflakes |
| `DISCORD_ALLOW_OPEN_GUILD` | `1` allows any guild member (dogfood only) |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.

## Adding a chat platform

The message handler is **transport-agnostic** — it takes
`(text, session, sink, logger)` and knows nothing about any platform. To add a
platform you do **not** touch the agent, prompts, or tools. You add one package
with the same five pieces `gateway/transports/telegram/` and `gateway/transports/slack/` both have:

1. **Settings** (`settings.py`): env-backed config, raising
   `GatewayConfigurationError` (from `gateway/core/runtime/errors.py`) when missing.
2. **A listener** (`startup.py` + the transport worker): receives inbound
   messages and calls the shared handler with `(text, session, sink, logger)`.
3. **Inbound security**: authorize each message and audit-log it
   (`integrations/messaging_security`).
4. **An output sink** (implement `GatewaySink` from
   `gateway/core/runtime/sink_protocol.py`): streams status and delivers the answer.
5. **Session binding** via `gateway/core/storage/session/resolver.py` with a new
   `platform` value: map the platform conversation key to a `Session`.

Then wire it in the composition root (`GatewayManager` in
`gateway/core/runtime/manager.py`) beside the existing transports. Reuse the handler
from `GatewayTurnHandler(...)` as-is.

**What you never change:** `GatewayTurnHandler`, `Agent`, prompts, tools.
Keeping the handler transport-agnostic is exactly what makes a new platform a small,
self-contained add.
