---
service: payments-api
triggers:
  - oom
  - memory
  - failure
category: resource_exhaustion
title: Payments API OOM playbook
---
# Payments API OOM playbook

When `payments-api` pods restart with exit code 137:

1. Bump JVM `-Xmx` from 1.5G → 2G in the `payments-api` Helm values.
2. Page `#payments-oncall` before scaling — owner approval required.
3. Verify recovery via the Grafana dashboard `payments-api/jvm-memory`.
