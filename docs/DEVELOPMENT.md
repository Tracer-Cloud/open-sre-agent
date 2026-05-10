# Development guide

Contributor-focused workflows: local setup details stay in [SETUP.md](../SETUP.md) at the repo root (Windows, troubleshooting, MCP/OpenClaw).

## Clone and install

```bash
git clone https://github.com/Tracer-Cloud/opensre.git
cd opensre
make install
```

[`make install`](../Makefile) runs `uv sync --frozen --extra dev` and the analytics install helper. Use **`uv run opensre …`** from the repo root so you always hit this checkout’s `.venv`, not another `opensre` on your `PATH`.

```bash
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

## Quality gates (same as CI)

From the repo root:

```bash
make lint          # ruff check
make format-check  # ruff format --check (CI-enforced)
make typecheck     # mypy app/
make test-cov      # pytest + coverage (default unit suite)
```

One-shot (includes heavier `test-full`): `make check`.

Before a PR, run at least `make lint`, `make format-check`, `make typecheck`, and `make test-cov` (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## VS Code dev container

The dev container is defined under [`.devcontainer/`](../.devcontainer/). It builds from [`.devcontainer/Dockerfile`](../.devcontainer/Dockerfile) (Python **3.13**), then **`postCreateCommand`** creates `.venv-devcontainer` and runs **`pip install -e '.[dev]'`** (not `uv`). Docker Desktop, OrbStack, Colima, or another compatible runtime must be available on the host.

## Benchmark

```bash
make benchmark
```

To refresh README benchmark copy from cached results (no LLM calls): `make benchmark-update-readme`.

## Deployment

### LangGraph Platform (official)

1. Create a deployment on LangGraph Platform and connect this repository.
2. Keep [`langgraph.json`](../langgraph.json) at the repo root.
3. Set `LLM_PROVIDER` and the matching API key (for example `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` — see [`.env.example`](../.env.example)).
4. Add integration and storage env vars your deployment needs.

Minimal LLM env:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=...
```

### Railway (self-hosted alternative)

Ensure the Railway project has Postgres and Redis and that the OpenSRE service has **`DATABASE_URI`** and **`REDIS_URI`** wired to them before deploying.

```bash
opensre deploy railway --project <project> --service <service> --yes
```

If the service never becomes healthy, confirm both URIs are set on the service.

### Remote hosted ops (Railway)

After deploy:

```bash
opensre remote ops --provider railway --project <project> --service <service> status
opensre remote ops --provider railway --project <project> --service <service> logs --lines 200
opensre remote ops --provider railway --project <project> --service <service> logs --follow
opensre remote ops --provider railway --project <project> --service <service> restart --yes
```

OpenSRE remembers the last `provider`, so you can shorten to:

```bash
opensre remote ops status
opensre remote ops logs --follow
```

## Telemetry and privacy

`opensre` ships with two telemetry stacks, both opt-out:

- **PostHog** — anonymous product analytics (commands used, success/failure, rough runtime, CLI/Python/OS/arch, and limited command metadata).
- **Sentry** — crashes and errors (stack traces, environment, release).

Events are tagged with `entrypoint`, `opensre.runtime`, and `deployment_method`. Sensitive headers, paths, and secret-shaped keys are scrubbed before send.

A random install ID is stored under `~/.config/opensre/anonymous_id`. PostHog `distinct_id` is scoped to that ID. Telemetry is off in GitHub Actions and pytest.

### Kill-switch matrix

| Env var                        | PostHog    | Sentry     |
| ------------------------------ | ---------- | ---------- |
| `OPENSRE_NO_TELEMETRY=1`       | disabled   | disabled   |
| `DO_NOT_TRACK=1`               | disabled   | disabled   |
| `OPENSRE_ANALYTICS_DISABLED=1` | disabled   | unaffected |
| `OPENSRE_SENTRY_DISABLED=1`    | unaffected | disabled   |

Full opt-out:

```bash
export OPENSRE_NO_TELEMETRY=1
```

### Sentry DSN

Self-hosted users can set `SENTRY_DSN` to their project; unset uses the bundled default. `SENTRY_DSN=` (empty) drops events in `before_send`.

### Deployment tagging

Set `OPENSRE_DEPLOYMENT_METHOD` to `railway`, `langsmith`, or `local` (default `local`) to label Sentry events.

### Local PostHog event log

By default, outbound PostHog payloads are also appended to `~/.config/opensre/posthog_events.txt` (rotates at 1000 lines). Disable:

```bash
export OPENSRE_ANALYTICS_LOG_EVENTS=0
```

We do not collect alert contents, file contents, hostnames, credentials, raw CLI arguments, or PII by design.
