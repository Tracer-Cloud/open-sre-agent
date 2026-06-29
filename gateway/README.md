# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms. v1 ships Telegram DM text chat.

## Quick start (local dev)

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Option A: start the interactive shell — poll-mode gateway starts automatically
# when TELEGRAM_BOT_TOKEN is set (TELEGRAM_GATEWAY_AUTO_START=true by default)
uv run opensre

# Option B: run the gateway as a dedicated process
uv run opensre gateway telegram --poll
```

DM your bot from Telegram.

## Production (webhook)

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_WEBHOOK_URL=https://your-host/telegram/webhook
export TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)
export TELEGRAM_ALLOWED_USERS=123456789

uv run opensre gateway telegram
```

The process binds `TELEGRAM_GATEWAY_HOST` (default `127.0.0.1`) and `TELEGRAM_WEBHOOK_PORT` (default `8443`).

## Architecture

- `gateway/runner.py` — routes inbound updates, per-user session locks
- `gateway/session/` — SQLite binding from Telegram user id → `ReplSession` JSONL file
- `gateway/turn_executor.py` — runs `execute_shell_turn` with Telegram output sink
- `gateway/approvals/` — inline Approve/Deny for external/mutating tools
- `gateway/sinks/telegram_sink.py` — typing + throttled `editMessageText` streaming

State lives in `~/.opensre/gateway/state.db`. Conversation transcripts use the normal `~/.opensre/sessions/*.jsonl` store.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_WEBHOOK_URL` | Public webhook URL (omit for poll mode) |
| `TELEGRAM_WEBHOOK_SECRET` | Required with webhook URL |
| `TELEGRAM_WEBHOOK_PORT` | Webhook listen port (default 8443) |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_HOST` | Bind host for webhook server |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (default 4) |
| `TELEGRAM_GATEWAY_AUTO_START` | When `true` (default), `opensre` starts poll-mode gateway if `TELEGRAM_BOT_TOKEN` is set |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.
