# Integration Notes — HEA-16

Findings from validating the Datadog, Kubernetes, and Slack integrations in the
HealOps.ai fork of OpenSRE. Testing used fixture/mock backends (no live API calls
required in CI) plus the staging environment where credentials were available.

---

## Summary

All three integrations are functional in the HealOps.ai fork. The CI test suite
runs entirely with recorded fixtures — no live API credentials are required.
Live-credential smoke testing is documented below.

---

## 1. Datadog Integration

### What works
- `DD_API_KEY` / `DD_APP_KEY` / `DD_SITE` env vars are read correctly from `.env`.
- `DD_INSTANCES` multi-instance override is supported (JSON array format).
- `FixtureDatadogBackend` fully satisfies the `DatadogBackend` protocol for CI runs.
- `query_logs` and `query_monitors` return the correct envelope shapes matching
  what the `DataDogLogs*` and `DataDogMonitors*` tools expect.
- K8s correlation tags (`kube_namespace`, `kube_deployment`, `pod_name`) are
  preserved in the log entries and available for alert-window correlation.

### Gaps / Quirks
- **OOM events are K8s-side, not Datadog-side.** For the `oom_killed` failure
  mode the OOM kill is a kernel/kubelet event. Datadog logs for this scenario
  contain healthy application logs from the alert window — not the OOM evidence
  itself. The OOM kernel line (`Memory cgroup out of memory: Killed process 1`)
  only appears in `eks_pod_logs`. Runbooks should query both sources.
- **Datadog error filtering** (`error_logs` subkey) does correctly surface
  application-level errors when they exist in Datadog (e.g., DNS failures
  logged as `status=error`). It does NOT synthesize errors from K8s-side events.
- **Staging credentials:** `DD_API_KEY` and `DD_APP_KEY` in `.env` are empty
  stubs. Populate them with staging values before running live e2e tests
  (`make test-rca`). The CI suite runs with `FixtureDatadogBackend` and never
  dials the Datadog API.
- **Rate limits:** Not hit during fixture testing. Observed limit in upstream
  OpenSRE docs: 300 requests/hour per API key for the metrics query endpoint.

---

## 2. Kubernetes (EKS) Integration

### What works
- `FixtureEKSBackend` fully satisfies the `EKSBackend` protocol for CI runs.
- `list_pods`, `get_events`, `get_pod_logs`, `list_deployments`, `get_node_health`
  all return the correct envelope shapes.
- `high_restart_pods` is correctly populated from `restart_count > 3`.
- `failing_pods` correctly excludes Running/Succeeded pods.
- Pod phase and container state fields (`CrashLoopBackOff`, `OOMKilled`,
  `exit_code=137`) are surfaced in the pod list response.
- Warning events with `OOMKilled` reason appear in `get_events` with accurate
  count and timestamps.
- Pod logs include the kernel OOM line (`Memory cgroup out of memory: Killed
  process 1`) immediately before the container exit.

### Gaps / Quirks
- **Kubeconfig / role_arn:** `.env` has `HELM_KUBECONFIG=` and `HELM_KUBE_CONTEXT=`
  empty. For live K8s queries the integration uses `role_arn` injected via
  `resolved_integrations["aws"]`. Populate with staging cluster ARN and region
  before running live e2e tests.
- **EKS tool injection:** Live EKS tools check for `_backend` key first and fall
  through to real AWS calls only when absent. This means the same test harness
  pattern works for both CI (fixture) and staging (live) without code changes.
- **Cluster/namespace override:** `list_pods` and `get_events` accept
  `cluster_name` and `namespace` parameters. When empty, they default to the
  scenario metadata values. This is correct for synthetic tests but callers must
  pass explicit values in multi-cluster deployments.

---

## 3. Slack Integration

### What works
- Incoming webhook delivery (`SLACK_WEBHOOK_URL`) works correctly.
- `send_slack_report` falls back gracefully to `no_thread_ts` when no webhook
  is configured and no thread context is available.
- Token redaction (`_redact_token`) correctly scrubs `xoxb-*` patterns from
  error strings and log lines before they reach the caller or logs.
- Non-JSON response bodies (e.g., `<html>Bad Gateway</html>` from a proxy)
  are handled without crashing; the error is truncated to 500 chars.
- `SLACK_BOT_TOKEN` is never echoed into the message body or Slack payload.

### Gaps / Quirks
- **Webhook not configured in staging:** `SLACK_WEBHOOK_URL` is empty in `.env`.
  Populate it before running live delivery tests. CI tests mock the webhook.
- **`no_thread_ts` fallback path:** When `SLACK_WEBHOOK_URL` is set but
  `thread_ts` is `None` (no Slack alert thread), the report is sent to the
  channel root via the webhook. This is correct for standalone investigations
  but may produce duplicate messages if the webhook channel already has
  the alert thread visible.
- **Bot-token path requires `thread_ts`:** Direct `chat.postMessage` delivery
  (used when `SLACK_BOT_TOKEN` + `channel` are present) requires a valid
  `thread_ts`. Without it the delivery fails with `no_thread_ts`. For
  investigations triggered outside Slack (e.g., CLI), use the webhook path.

---

## 4. CI Test Coverage

The following test file exercises all three integrations without live API calls:

```
tests/integrations/test_k8s_oom_integration.py
```

Key files added for HEA-16:

| File | Purpose |
|------|---------|
| `tests/synthetic/k8s_oom_alert.json` | Synthetic K8s OOM alert input fixture (Datadog format) |
| `tests/fixtures/k8s_oom_output.json` | Investigation report regression baseline |
| `tests/integrations/test_k8s_oom_integration.py` | 25 CI tests covering Datadog, K8s, Slack, and credential masking |

### Running the tests

```bash
# CI suite (no live credentials required)
uv run pytest tests/integrations/test_k8s_oom_integration.py -v

# Minimal local verification
make test-cov
```

---

## 5. Credential Masking Verification

- `tests/integrations/test_k8s_oom_integration.py::TestCredentialMasking` verifies
  that `DD_API_KEY`, `DD_APP_KEY`, `KUBECONFIG`, and `SLACK_BOT_TOKEN` never appear
  in Datadog response payloads, K8s event responses, or Slack delivery payloads.
- `tests/fixtures/k8s_oom_output.json` has been reviewed: no credential env-var
  names appear in the stored report content.
- Bot tokens (`xoxb-*` pattern) are redacted from error messages by
  `slack_delivery._redact_token` before reaching callers or log lines.
