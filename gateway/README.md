
## Architecture Notes By Vincent (June 30th 2026)
- Target initial support for Telegram and Slack.
- Issues: Headless agent lacks full integration initialization; current target path may not be optimal.
- Treat the messaging gateway as a distinct surface area.
- Goal: Fully decouple the gateway from other packages --> if this is true then it means that the gateway is configurable through dependency injection to call other agents.

**Key Problem Right Now**
- The critical problem however, right now is that we need to be able to spin up an agent and load integrations from it.

# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms: Telegram DM text chat
via long polling, and Slack mentions/DMs via Socket Mode.

## Entry points

| What you want | File / symbol | How it is started |
|---------------|---------------|-------------------|
| **Package main** | `gateway/main.py` → `main()` | `python -m gateway.main` or `opensre gateway start` |
| **Composition root (impl)** | `gateway/runtime/manager.py` → `GatewayManager` / `main()` | Called by `gateway.main` |
| **Background daemon helpers** | `gateway/runtime/daemon.py` | Used by CLI `gateway start/stop/status` (pidfile + `components.json`) |
| **HTTP API (web-only task)** | `gateway/http/webapp.py` → `app` | `uvicorn gateway.http.webapp:app` (`MODE=web` in Docker) |
| **Telegram transport** | `gateway/telegram/wiring.py` → `start_telegram_worker` | Started by `GatewayManager._start_telegram` |
| **Slack transport** | `gateway/slack/wiring.py` → `start_slack_worker` | Started by `GatewayManager._start_slack` |
| **Per-message turn** | `gateway/runtime/turn_handler.py` → `GatewayTurnHandler` | Injected into both transports as the agent callback |

```text
opensre gateway start
        │
        ▼
gateway.runtime.daemon.start_gateway_daemon
        │  spawns: python -m gateway.main
        ▼
gateway/main.py
        │
        ▼
gateway.runtime.manager.GatewayManager.start_gateway
        ├── http/web_server  →  http/webapp:app
        ├── telegram/wiring.start_telegram_worker
        ├── slack/wiring.start_slack_worker
        └── scheduler
```

Do **not** look for entry modules at the package root — they moved under
`runtime/`, `http/`, `telegram/`, and `slack/`.

## How the pieces fit (surfaces, gateway, integrations)

Three things that are easy to mix up:

- **Surface** — a way a person talks *to* the agent (message in, answer out). Today
  there are three: the interactive shell (`surfaces/interactive_shell`, you type in
  a terminal), the CLI one-shot (`surfaces/cli`, one command → one answer), and the
  **gateway** (`gateway/`, you chat with the agent from a chat app).
- **Gateway** — one specific surface: the always-on process that connects a chat app
  to the agent. It speaks **Telegram** (long poll) and **Slack** (Socket Mode).
- **Integrations + tools** — the *outbound* side: the agent sending a message *out*
  to a channel. `integrations/telegram` and `integrations/slack` deliver messages;
  the agent calls the `telegram_send_message` / `slack_send_message` tools to do it.

Both platforms are symmetric:

| | Inbound (person → agent) | Outbound (agent → channel) |
|---|---|---|
| **Telegram** | Yes — `gateway/telegram/` | Yes — integration + tool |
| **Slack** | Yes — `gateway/slack/` (Socket Mode; each thread is a conversation) | Yes — integration + tool |

**One core for every surface.** Shell, CLI, and the gateway transports all hand the
message to the same place: a `HeadlessAgent` (`agent.dispatch(message)`). They differ
only in *how they receive input and send output* — never in how the agent thinks.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Start the gateway daemon (web app + Telegram chat + Slack chat + task scheduler)
uv run opensre gateway start
```

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

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.

## Adding a chat platform

The message handler is **transport-agnostic** — it takes
`(text, session, sink, logger)` and knows nothing about any platform. To add a
platform you do **not** touch the agent, prompts, or tools. You add one package
with the same five pieces `gateway/telegram/` and `gateway/slack/` both have:

1. **Settings** (`settings.py`): env-backed config, raising
   `GatewayConfigurationError` (from `gateway/runtime/errors.py`) when missing.
2. **A listener** (`wiring.py` + the transport worker): receives inbound
   messages and calls the shared handler with `(text, session, sink, logger)`.
3. **Inbound security**: authorize each message and audit-log it
   (`integrations/messaging_security`).
4. **An output sink** (implement `GatewaySink` from
   `gateway/runtime/sink_protocol.py`): streams status and delivers the answer.
5. **Session binding** via `gateway/storage/session/resolver.py` with a new
   `platform` value: map the platform conversation key to a `Session`.

Then wire it in the composition root (`GatewayManager` in
`gateway/runtime/manager.py`) beside the existing transports. Reuse the handler
from `GatewayTurnHandler(...)` as-is.

**What you never change:** `GatewayTurnHandler`, `Agent`, prompts, tools.
Keeping the handler transport-agnostic is exactly what makes a new platform a small,
self-contained add.
