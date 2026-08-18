# Platform

`platform/` contains shared runtime services that sit outside the user-facing
application package and outside the core agent harness loop.

Platform code may own side effects such as telemetry emission, audit logging,
runtime display, tracing, auth verification, masking, sandbox execution, and
minimal guardrails. Configuration-only behavior belongs in `config/`; agent
orchestration, state, tool planning, and tool execution contracts belong in
`core/`.

Name packages by **what they do**. Do not add a `common/` / `shared/` / `util/`
junk drawer.

Areas:

- `auth/` — runtime authentication and identity checks.
- `analytics/` — product and runtime analytics.
- `errors/` — `OpenSREError` contract (any layer may raise/catch).
- `process/` — process exit codes and CLI runtime flags.
- `tasks/` — in-flight task types and the persistent task registry.
- `turn_capacity/` — process-wide turn concurrency gates.
- `release_version.py` — installed vs latest release version helpers.
- `service_families/` — tool-availability family-key normalization.
- `text/` — truncate / coerce / URL validation helpers.
- `evidence/` — log and evidence compaction for prompt-sized results.
- `cloudflare_install_proxy/` — Cloudflare Worker for `install.opensre.com`.
- `deployment_ec2/` — EC2 AWS primitives and Telegram gateway AMI/systemd deploy (`telegram_gateway/`). Makefile: `make deploy-gateway`.
- `notifications/` — notification delivery transports and channel-specific senders.
- `observability/` — logging, tracing, progress, debug output, and runtime
  display ports.
- `masking/` — reversible masking and identifier normalization.
- `scheduler/` — cron-driven scheduled deliveries, task persistence, and
  execution deduplication.
- `sandbox/` — constrained execution environments.
- `guardrails/` — minimal runtime safety checks outside the core agent loop.

Future migrations should move existing modules into this folder incrementally
with import updates and tests. Avoid compatibility-only forwarding modules;
each migration should leave one canonical import path.
