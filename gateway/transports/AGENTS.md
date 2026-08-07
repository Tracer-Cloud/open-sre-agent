# gateway/transports/ — chat peer packages

One package per inbound chat platform. Peers: **none imports another**.

| Package | Start |
|---------|--------|
| `slack/` | `startup.start_slack_worker` |
| `discord/` | `startup.start_discord_worker` |
| `telegram/` | `startup.start_telegram_worker` |

Composition of peers lives in `gateway.channels` (`chat.py` + `compose.py`),
not here. `GatewayManager` only holds the opaque `ChannelsHandle`.
Transport-specific work (settings load, Discord readiness wait) stays in each
package's `startup.py`.

Each owns settings, inbound worker, security, output sink, and `startup.py`.
Anything two transports need belongs in `gateway.core` (usually
`gateway.core.runtime`).

Peers must not import `gateway.channels` or `gateway.web`.

Peer import DAG pinned by `gateway/tests/test_package_borders.py`.
Discord↔Slack isolation extras:
`gateway/tests/discord/test_transport_borders.py`.
