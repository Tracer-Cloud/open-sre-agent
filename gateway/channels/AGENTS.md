# gateway/channels/ — consumer composition

Sole module that starts/stops the gateway's consumer surfaces together.

| May import | Must not |
|------------|----------|
| `gateway.core.*` | be imported by `transports.*` or `web` |
| `gateway.web.*` | |
| `gateway.transports.*.startup` | peer transport internals beyond wiring |

| File | Role |
|------|------|
| `__init__.py` | Public API: `start_channels`, `ChannelsHandle`, `ChannelName` |
| `compose.py` | Web + chat as one unit |
| `chat.py` | Chat transport registry (`start_transports` / `stop_transports`) |

Scheduler is **not** a channel — `GatewayManager.start_scheduler` starts it as a
peer after `start_channels` returns.

Pinned by `gateway/tests/test_package_borders.py`.
