# gateway/transports/ — chat peer packages

One package per inbound chat platform. Peers: **none imports another**.

| Package | Start |
|---------|--------|
| `slack/` | `wiring.start_slack_worker` |
| `discord/` | `wiring.start_discord_worker` |
| `telegram/` | `wiring.start_telegram_worker` |

Each owns settings, inbound worker, security, output sink, and wiring.
Anything two transports need belongs in `gateway.core` (usually
`gateway.core.runtime`).

Peer import DAG pinned by `gateway/tests/test_package_borders.py`.
Discord↔Slack isolation extras:
`gateway/tests/discord/test_transport_borders.py`.
