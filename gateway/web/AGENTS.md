# gateway/web/ — web surface

FastAPI app for health probes, alert intake, and investigations.
Not a chat transport — no turn-handler / sink wiring.

| May import | Must not |
|------------|----------|
| `gateway.core.*` | `gateway.transports.*` |

Entry: `webapp.py` (`app`) — `uvicorn gateway.web.webapp:app` when `MODE=web`,
or daemon / shell via `web_server.serve_webapp_in_thread`.

Pinned by `gateway/tests/test_package_borders.py`.
