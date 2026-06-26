# Platform

`platform/` contains shared runtime services that sit outside the user-facing
application package and outside the core agent harness loop.

Platform code may own side effects such as telemetry emission, audit logging,
runtime display, tracing, auth verification, masking, sandbox execution, and
minimal guardrails. Configuration-only behavior belongs in `config/`; agent
orchestration, state, tool planning, and tool execution contracts belong in
`core/`.

Initial areas:

- `auth/` owns runtime authentication and identity checks.
- `analytics/` owns product and runtime analytics.
- `common/` owns small shared helpers that do not belong to a runtime subsystem.
- `notifications/` owns notification delivery transports and channel-specific senders.
- `observability/` owns logging, tracing, progress, debug output, and runtime
  display ports.
- `masking/` owns reversible masking and identifier normalization.
- `scheduler/` owns cron-driven scheduled deliveries, task persistence, and
  execution deduplication.
- `sandbox/` owns constrained execution environments.
- `guardrails/` owns minimal runtime safety checks outside the core agent loop.

Future migrations should move existing modules into this folder incrementally
with import updates and tests. Avoid compatibility-only forwarding modules;
each migration should leave one canonical import path.

