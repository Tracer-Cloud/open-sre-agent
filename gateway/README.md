
## Architecture Notes By Vincent (June 30th 2026)
- Target initial support for Telegram and Slack.
- Issues: Headless agent lacks full integration initialization; current target path may not be optimal.
- Treat the messaging gateway as a distinct surface area.
- Goal: Fully decouple the gateway from other packages --> if this is true then it means that the gateway is configurable through dependency injection to call other agents.

**Key Problem Right Now**
- The critical problem however, right now is that we need to be able to spin up an agent and load integrations from it.

# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms. v1 ships Telegram DM text chat via long polling.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Run the gateway as a dedicated process
uv run opensre gateway telegram
```

DM your bot from Telegram.


## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (default 4) |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.
