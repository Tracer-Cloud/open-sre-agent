# Scenario 021 — API Surge Driving RDS CPU Near-Critical, No Correlation ID

## Overview

| Field | Value |
|---|---|
| Instance | `payments-prod` (Postgres 15) |
| Failure Mode | Upstream API traffic surge driving DB CPU |
| Symptoms | RDS CPU at 92%, modest connection climb, one query dominating PI |
| Severity | Critical |
| Difficulty | Level 4 (compositional, adversarial) |

This scenario validates that the agent can identify an upstream traffic surge as the root cause of a near-critical RDS CPU alert, when the upstream signal does not light up CPU on the API tier and there is no correlation ID linking API requests to database queries. Inspired by real customer feedback (head of DevOps, May 2026) on the underexplored "RDS CPU climbing from API request volume" pattern.

---

## Ground Truth

- **Root cause category**: `application_load_spike`
- **Root cause**: A 5x surge in inbound API requests to the `prod-api-tg` target group drives the products-by-category SELECT call rate from ~16/sec to ~82/sec, which accounts for ~77% of total DB load
- **The DB is healthy in shape**: the query has a normal plan, normal per-call duration, no locks
- **The load is volume, not cost**

---

## What makes this hard (vs 015)

Scenario 015 also tests upstream EC2 load attribution, but its signal pattern is **easy**: the web tier CPU climbs from 30% to 85%, so the agent can correlate by CPU shape. Scenario 021 is harder because:

- **API tier CPU stays flat (~25-28%)** — the requests are I/O-bound for the API process and CPU-bound for the database, so the API tier does not light up on CPU
- The only upstream CPU-tier signal is **NetworkOut** and **ALB RequestCount** — non-CPU metrics that the agent has to think to look at
- DatabaseConnections climbs only modestly (the API connection pool absorbs the surge) — so `connection_exhaustion` is also the wrong category
- Performance Insights shows one query dominating, but its per-call duration is normal — the agent has to reason about volume vs cost

The agent that just looks at "where is CPU elevated" finds only the DB and stops. The agent that tests `connection_exhaustion` because connections grew finds the wrong category. The agent that flags the parameter-group change finds an unrelated event from 6 hours earlier.

---

## Expected Behaviour

A correct agent must:

- Identify the products-by-category SELECT as the dominating query
- Recognize that per-call duration is normal (~18 ms) — the load is volume not cost
- Look at ALB RequestCount (or EC2 NetworkOut on the API tier) and find the 5x surge
- Note that API tier CPU stayed flat, ruling out an API-side CPU regression
- Note that DatabaseConnections climbed only modestly, ruling out connection exhaustion
- Note that the background tier remained flat, ruling it out
- Rule out the parameter group change as a temporally-disjoint red herring
- Conclude: upstream API request surge → DB CPU near-critical

---

## Required Reasoning Elements

The response should include:

- API request surge as the root cause
- Reference to ALB RequestCount or API tier NetworkOut climbing 5x
- The dominating query (products-by-category SELECT)
- "Volume not cost" framing: per-call duration is normal, only the call rate climbed (from ~16/sec to ~82/sec)
- Explicit dismissal of: cpu_saturation, connection_exhaustion, parameter group change, background tier

---

## Strict Validation Rules

- Category must be `application_load_spike` (or one of the accepted equivalents: `upstream_traffic_surge`, `application_tier_load_spike`, `cpu_saturation_workload_burst`, `cpu_saturation_bad_query`)
- Must NOT classify as `cpu_saturation`, `configuration_error`, `replication_lag`, or `storage_full`
- Must include keywords proving the agent traced the surge: `API`, `request`, `surge`, `products`, `CPU`, `92`
- Must NOT use phrases that suggest wrong diagnosis: `configuration_error`, `slow query`, `cpu_saturation`
- Should explicitly dismiss the red-herrings (Axis 2 `ruling_out_keywords`, soft signal): `flat` (API tier CPU is flat), `background tier`, `parameter group`

### Notes on the equivalent categories

The agent sometimes labels this scenario `cpu_saturation_workload_burst` or `cpu_saturation_bad_query` instead of `application_load_spike`. Both labels are accepted because the agent's reasoning content is what determines diagnosis quality, not the label string. The ideal label is `application_load_spike` (matching scenarios 015 and 020 which test the same failure family), and tightening category vocabulary across the suite is a follow-up beyond this scenario.

---

## Failure Modes

The scenario should fail if the agent:

- Classifies the root cause as `cpu_saturation` (treats symptom as cause without identifying the upstream surge)
- Classifies as `connection_exhaustion` (wrong direction — connections grew only modestly because the API pool absorbed the surge)
- Blames the parameter group change six hours before the alert
- Blames the products-by-category query as a `slow query` (its plan is normal and per-call duration is 18 ms)
- Blames the background tier
- Identifies the surge but fails to articulate the cause in reasoning

---

## Passing Criteria

A correct response:

- Identifies the upstream API request surge as the cause
- Cites ALB RequestCount and/or API tier NetworkOut as evidence of the surge (typically +400% or ~5x)
- Cites the products-by-category SELECT as the dominating query and explains the load is volume, not query cost
- Explicitly rules out `cpu_saturation`, `connection_exhaustion`, the parameter group change, and the background tier (Axis 2)
- Uses category `application_load_spike` or an accepted equivalent
