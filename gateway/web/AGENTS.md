# gateway/web/ — web surface

FastAPI app for health probes, alert intake, and investigations.
Not a chat transport — no turn-handler / sink wiring.

| May import | Must not |
|------------|----------|
| `gateway.core.*` | `gateway.transports.*`, `gateway.channels` |

Entry: `webapp.py` (`app`) — `uvicorn gateway.web.webapp:app` when `MODE=web`,
or daemon / shell via `web_server.serve_webapp_in_thread`.

Pinned by `gateway/tests/test_package_borders.py`.

## Process boot (`WEB_PROFILE`)

Importing `gateway.web.webapp` (uvicorn or in-process via
`serve_webapp_in_thread`) runs `configure_process(WEB_PROFILE)` — env, Sentry
(`webapp`), and harness adapters. That is intentional so `MODE=web` and
embedded web both get Path-2 investigate wiring without a CLI/manager boot. Do
**not** add a second harness-registration site in `web/`; adapters stay in
`bootstrap.adapters` only. In a full gateway process, `GATEWAY_PROFILE` already
ran; `WEB_PROFILE` may still run (separate idempotency key) — steps must stay
safe to re-enter, not invent a divergent registry.

## Capacity (Path-2)

`POST /investigate` and `InvestigationWorker` take
:func:`~gateway.core.runtime.concurrency.process_turn_gate` — the same
process gate as chat/scheduler (`OPENSRE_SIZE_PROFILE`). Sync HTTP
`try_acquire` → 503 when full; the worker `acquire`s (blocking) after claim.
Capacity is process gate + transport pools + Fargate fleet (same rules as the gateway package).
