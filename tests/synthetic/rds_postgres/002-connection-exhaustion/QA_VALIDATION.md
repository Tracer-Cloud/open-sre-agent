# Scenario 002 - Connection Pool Leak

## Overview

This scenario validates that the agent identifies connection exhaustion caused by a client-side pool leak, and treats the concurrent CPU elevation as a secondary symptom rather than an independent failure.

The trap is the 35-50% CPU rise: it is a real signal, but it is a downstream effect of accumulated idle sessions making periodic lightweight queries. The agent must not classify the incident as `cpu_saturation`.

## Correct Diagnosis

| Field | Expected |
|---|---|
| Root cause category | `resource_exhaustion` |
| Root cause | Client-side connection pool leak exhausted `max_connections` |
| Affected component | Primary RDS instance with leaked client sessions |
| Primary symptom | New connection attempts rejected as `max_connections` is reached |
| Secondary symptom | CPUUtilization 35-50% from idle session housekeeping queries |
| Non-root cause | CPU saturation, slow query, or bad SQL plan |

## Required Reasoning

A correct answer should include the full causal chain:

1. A client-side pool leak holds open sessions instead of returning them.
2. Idle sessions accumulate; `DatabaseConnections` climbs to ~490 of 500.
3. Idle sessions issue periodic lightweight queries, lifting CPUUtilization to 35-50% as a side effect.
4. New application requests fail to connect; the alert fires on `max_connections` exhaustion.
5. Performance Insights shows `Client:ClientRead` as the dominant wait event, confirming idle-session pressure rather than query workload.

## Evidence Expectations

The agent should cite evidence such as:

- `aws_cloudwatch_metrics`: `DatabaseConnections` near 490/500, CPUUtilization 35-50%
- `aws_performance_insights`: `Client:ClientRead` as the dominant wait event, no expensive SQL dominating db_load

## Pass Criteria

The answer is considered correct if it:

- Identifies connection exhaustion as the root cause
- References `max_connections` and the near-cap connection count
- Explains that the CPU elevation is a secondary effect of idle sessions, not an independent CPU saturation
- Cites Performance Insights `ClientRead` waits as the discriminating signal
- Uses both metric-level and query-level evidence

## Common Failure Modes

The answer should be considered incorrect if it:

- Diagnoses `cpu_saturation` based on the 35-50% CPU rise
- Says "connection exhaustion" without explaining the idle-session leak pattern
- Treats CPU and connections as two independent problems instead of one causal chain
- Blames a slow query or missing index without checking Performance Insights wait events
- Omits Performance Insights from the evidence chain
