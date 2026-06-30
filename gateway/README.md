# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms. v1 ships Telegram DM text chat via long polling.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Option A: start the interactive shell — long-poll gateway starts automatically
# when TELEGRAM_BOT_TOKEN is set (TELEGRAM_GATEWAY_AUTO_START=true by default)
uv run opensre

# Option B: run the gateway as a dedicated process
uv run opensre gateway telegram
```

DM your bot from Telegram.

## Architecture

- `gateway/polling/handle_polled_inbound_telegram_msg.py` — auth, session, and agent dispatch for polled updates
- `gateway/storage/` — SQLite state (`db.py`) and session bindings from Telegram user id → `ReplSession` JSONL file
- `gateway/agent/dispatch_gateway_msg_to_agent.py` — runs the headless agent with gateway harness adapters (prompt grounding, action tools, reasoning)
- `gateway/agent/gateway_agent_adapters.py` — Telegram-specific harness port implementations
- `gateway/agent/gateway_action_tools.py` — gateway-local `shell_run` and `investigation_start` action tools
- `gateway/polling/telegram_gateway_background.py` — long-poll daemon thread (REPL auto-start and dedicated process)
- `gateway/agent/gateway_output_sink.py` — typing + throttled outbound message streaming
- `gateway/tests/` — package-local gateway regression tests

State lives in `~/.opensre/gateway/state.db`. Conversation transcripts use the normal `~/.opensre/sessions/*.jsonl` store.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (default 4) |
| `TELEGRAM_GATEWAY_AUTO_START` | When `true` (default), `opensre` starts long-poll gateway if `TELEGRAM_BOT_TOKEN` is set |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.
