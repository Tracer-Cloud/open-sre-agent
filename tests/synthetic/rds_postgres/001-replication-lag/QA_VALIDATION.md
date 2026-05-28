# Scenario 001 — WAL-driven Replication Lag

## Overview

This scenario validates that the agent identifies replication lag caused by a write-heavy workload on the primary database.

The key mechanism is not simply “replication lag”; the agent must explain that WAL generation on the primary outpaced WAL replay on the read replica.

## Correct Diagnosis

| Field | Expected |
|---|---|
| Root cause category | `resource_exhaustion` |
| Root cause | Write-heavy workload on the primary generated WAL faster than the read replica could replay it |
| Affected component | `payments-prod-replica-1` |
| Primary symptom | Replica lag / stale reads |
| Non-root cause | CPU saturation or connection exhaustion |

## Required Reasoning

A correct answer should include the full causal chain:

1. A write-heavy workload runs on the primary.
2. WriteIOPS and `TransactionLogsGeneration` increase.
3. WAL generation exceeds replica replay capacity.
4. `ReplicaLag` grows on `payments-prod-replica-1`.
5. Stale reads trigger the replication lag alert.

The replica is affected, but the initiating cause must be attributed to the primary workload.

## Evidence Expectations

The agent should cite evidence such as:

- `aws_cloudwatch_metrics`: `ReplicaLag`, `WriteIOPS`, `TransactionLogsGeneration`
- `aws_performance_insights`: write-heavy SQL activity with WAL-related waits (e.g. IO:WALWrite)

## Pass Criteria

The answer is considered correct if it:

- Identifies replication lag accurately
- Explains the WAL generation vs replay mismatch
- Mentions the replica explicitly
- Links the issue to a primary write-heavy workload
- Uses both metric-level and query-level evidence

## Common Failure Modes

The answer should be considered incorrect if it:

- Diagnoses `cpu_saturation` or `connection_exhaustion`
- Mentions replication lag without explaining the WAL generation/replay mechanism
- Blames the replica alone without identifying the primary workload
- Omits the replica from the causal chain
- Treats CPU as the initiating cause rather than a secondary effect
