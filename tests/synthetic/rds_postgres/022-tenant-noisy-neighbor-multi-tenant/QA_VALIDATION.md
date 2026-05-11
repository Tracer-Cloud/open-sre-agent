# Scenario 022 — Tenant Noisy Neighbor on a Shared Multi-Tenant Database

## Overview

| Field | Value |
|---|---|
| Instance | `payments-multi-prod` (Postgres 15) |
| Failure Mode | One tenant's traffic surge driving shared-DB CPU |
| Symptoms | RDS CPU at 91%, one query dominating PI, one user dominating PI top_users |
| Severity | Critical |
| Difficulty | Level 4 (compositional, adversarial) |

This scenario validates that the agent can identify the offending **tenant** in a multi-tenant database, not just the offending query. It is the first scenario in the suite to test tenant attribution through Performance Insights `top_users` and per-target-group ALB metrics.

---

## Ground Truth

- **Root cause category**: `application_load_spike`
- **Root cause**: A traffic surge on tenant `acme-corp` (~8x baseline request count) drives ~78% of database load on the shared primary. Other tenants (globex, initech) stay at baseline.
- **The DB is healthy in shape**: the dominating query has a normal plan, normal per-call duration (22 ms), and no contention.
- **The load is one-sided** — not a system-wide regression and not a shared-upstream cause.

---

## What makes this hard (vs 021)

Scenario 021 tests "find the upstream cause" — the agent has to look at non-CPU metrics on the API tier to discover an ALB request surge.

Scenario 022 is harder for a different reason: the dominating SQL pattern is used by every tenant, so identifying the query alone is not enough — the agent has to identify **which tenant** is responsible. That requires reading Performance Insights `top_users` (the only place tenant attribution lives in this fixture) and cross-referencing with `ec2_instances_by_tag` to map DB-side user activity back to a specific tenant's infrastructure.

This is the first synthetic scenario where `top_users` is the load-bearing piece of evidence.

---

## Expected Behaviour

A correct agent must:

- See the RDS CPU climb and recognize this is a load-driven event
- Read Performance Insights and find one query dominating
- Read PI `top_users` and recognize one tenant's DB user dominates the load while other tenant users remain at baseline
- Cross-check `ec2_instances_by_tag` to map the DB user to the tenant's API tier instances
- Cross-check per-tenant ALB RequestCount to confirm the surge is concentrated on one target group (acme's) and not on the others
- Explicitly rule out the other tenants as non-contributors
- Recognize that the query itself is fine — its plan and duration are unchanged; only one tenant's call rate is amplified
- Recommend tenant-specific remediation (per-tenant rate limits, contacting acme's team, considering isolation)

---

## Required Reasoning Elements

The response should include:

- The offending tenant (`acme-corp` or `acme`) as the root cause
- The phrase `tenant` or equivalent reasoning about per-customer attribution
- Reference to the dominating query (orders+order_items join by customer_id)
- Reference to PI top_users showing `acme_app_user` dominant
- Explicit dismissal of the other tenants (globex, initech) and the background worker
- "Volume not cost" framing: per-call duration is normal, only one tenant's call rate is amplified

---

## Strict Validation Rules

- Category must be `application_load_spike` (or accepted equivalents: `tenant_noisy_neighbor`, `multi_tenant_load_spike`, `application_tier_load_spike`, `upstream_traffic_surge`, `cpu_saturation_bad_query`, `cpu_saturation_workload_burst`)
- Must NOT classify as `cpu_saturation`, `connection_exhaustion`, `configuration_error`, `replication_lag`, or `storage_full`
- Must include keywords proving tenant attribution: `tenant`, `acme`, `customer`, `CPU`, `91`
- Must NOT use phrases that suggest wrong diagnosis: `configuration_error`, `slow query`, `schema regression`
- Should explicitly mention the other tenants as ruled out (`globex`, `initech`, `background`, `baseline`)

### Notes on the equivalent categories

The agent sometimes labels this scenario `cpu_saturation_bad_query` or `cpu_saturation_workload_burst` rather than `application_load_spike`. Both labels are accepted because the agent's reasoning content (correctly identifying acme as the noisy neighbor) is what determines diagnosis quality. The ideal label is `application_load_spike` or `tenant_noisy_neighbor`; tightening the agent's category vocabulary across the suite is a separate follow-up.

The forbidden keyword list deliberately excludes `cpu_saturation` (substring overlap with the equivalent category labels). The `forbidden_categories` list still gates the wrong family with exact-match.

---

## Failure Modes

The scenario should fail if the agent:

- Classifies as `cpu_saturation` (treats symptom as cause)
- Classifies as `connection_exhaustion` (connections climbed modestly but the pool absorbed the surge)
- Blames the dominating query as a `slow query` or `schema regression` (the query plan is normal)
- Identifies the surge but does not name the responsible tenant
- Identifies one tenant but fails to dismiss the others
- Misattributes the load to the background tier

---

## Passing Criteria

A correct response:

- Names the offending tenant (`acme-corp`)
- Cites PI top_users / top_hosts or per-tenant ALB RequestCount as the attribution evidence
- Explains the query itself is fine — only one tenant's call rate is amplified
- Rules out the other tenants explicitly
- Recommends tenant-specific remediation
- Uses category `application_load_spike` or an accepted equivalent
