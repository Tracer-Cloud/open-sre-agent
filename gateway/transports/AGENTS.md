# gateway/transports/ — chat peer packages

One package per inbound chat platform. Peers: **none imports another**.

In API-framework terms, transports are the controllers: platform ingress that
authorizes, resolves a session, builds a sink, and calls the turn handler. The
contract they implement is `gateway.core.transport_api`; the shared per-turn
steps they compose are `gateway.core.middleware`; the facade that starts them
is `gateway.channels`.

| Package | Start |
|---------|--------|
| `slack/` | `startup.start_slack_worker` |
| `discord/` | `startup.start_discord_worker` |
| `telegram/` | `startup.start_telegram_worker` |
| `buzz/` | `startup.start_buzz_worker` |

Composition of peers lives in `gateway.channels` (`chat.py` + `compose.py`),
not here. `GatewayManager` only holds the opaque `ChannelsHandle`.
Transport-specific work (settings load, Discord readiness wait) stays in each
package's `startup.py`.

Each owns settings, inbound worker, security, output sink, and `startup.py`.
Anything two transports need belongs in `gateway.core` (per-turn steps in
`gateway.core.middleware`, contracts in `gateway.core.transport_api`).
Concern completeness per transport is pinned by
`gateway/tests/test_transport_contract.py` (with its known-gaps ledger).

Peers must not import `gateway.channels` or `gateway.web`.

Peer import DAG pinned by `gateway/tests/test_package_borders.py`.
Discord↔Slack isolation extras:
`gateway/tests/discord/test_transport_borders.py`.
