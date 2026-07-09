
## Architecture Notes By Vincent (June 30th 2026)
- Target initial support for Telegram and Slack.
- Issues: Headless agent lacks full integration initialization; current target path may not be optimal.
- Treat the messaging gateway as a distinct surface area.
- Goal: Fully decouple the gateway from other packages --> if this is true then it means that the gateway is configurable through dependency injection to call other agents.

**Key Problem Right Now**
- The critical problem however, right now is that we need to be able to spin up an agent and load integrations from it.

# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms. v1 ships Telegram DM text chat via long polling.

## How the pieces fit (surfaces, gateway, integrations)

Three things that are easy to mix up:

- **Surface** — a way a person talks *to* the agent (message in, answer out). Today
  there are three: the interactive shell (`surfaces/interactive_shell`, you type in
  a terminal), the CLI one-shot (`surfaces/cli`, one command → one answer), and the
  **gateway** (`gateway/`, you chat with the agent from a chat app).
- **Gateway** — one specific surface: the always-on process that connects a chat app
  to the agent. Right now it speaks **Telegram only**.
- **Integrations + tools** — the *outbound* side: the agent sending a message *out*
  to a channel. `integrations/telegram` and `integrations/slack` deliver messages;
  the agent calls the `telegram_send_message` / `slack_send_message` tools to do it.

So the two platforms are not symmetric today:

| | Inbound (person → agent) | Outbound (agent → channel) |
|---|---|---|
| **Telegram** | Yes — the gateway | Yes — integration + tool |
| **Slack** | Not yet — `surfaces/slack_app` is an empty stub | Yes — integration + tool |

A person can already receive messages the agent *sends* to Slack, but cannot yet
*chat to* the agent from Slack.

**One core for every surface.** Shell, CLI, and the Telegram gateway all hand the
message to the same place: a `HeadlessAgent` (`agent.dispatch(message)`). They differ
only in *how they receive input and send output* — never in how the agent thinks.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Start the gateway daemon (web app + Telegram chat + task scheduler)
uv run opensre gateway start
```

DM your bot from Telegram.


## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (default 4) |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.

## Adding a chat platform (e.g. Slack inbound)

The message handler is already **transport-agnostic** — it takes
`(text, session, sink, logger)` and knows nothing about Telegram. So to add Slack
inbound you do **not** touch the agent, prompts, or tools. You add three small
pieces, the same shape Telegram already has:

1. **A listener** (like `start_telegram_worker` in `gateway/telegram_gateway.py`):
   receives incoming Slack messages (Slack Events API or Socket Mode) and calls the
   shared handler with `(text, session, sink, logger)`.
2. **An output sink** (implement `GatewayOutputSink` from
   `gateway/gateway_output_sink.py`): its `stream()` / `finalize()` send text back to
   the Slack channel via `integrations/slack/delivery.py`.
3. **A session resolver** (like `gateway/storage/session/resolver.py`): map a Slack
   user + channel to a `Session`.

Then wire it in the composition root (`GatewayManager` in `gateway/manager.py`):
start the Slack listener next to (or instead of) Telegram. Reuse the handler from
`GatewayTurnHandler(...)` as-is.

**What you never change:** `GatewayTurnHandler`, `Agent`, prompts, tools.
Keeping the handler transport-agnostic is exactly what makes a new platform a small,
self-contained add.
