# Axis-memory test scaffold

Synthetic-suite scaffold for evaluating memory anchoring in OpenSRE
investigations. Pairs with [issue #1234 (Contextual Memory)][issue-1234].

## What this is

The `tests/synthetic/rds_postgres/` suite already covers a single
investigation per scenario in isolation. This scaffold adds a second axis
on top of that: pairs of scenarios that share an external alert signature
(same `alertname`, same `commonLabels`) but resolve to different
ground-truth outcomes.

The intent is to test what happens when a memory layer (#1234) is primed
from the first investigation in a pair and the second is run on top of it.
If memory makes the planner skip exploratory steps that the second
scenario needs, the planner's pass-rate on the sibling drops. That is the
anchoring failure mode the team flagged in the #1234 thread.

## Why pairs and not single cases

Memory anchoring is a cross-investigation effect. A single scenario can
only test whether the planner is rigorous against red herrings within one
trajectory. Anchoring needs at least two runs that share an alert
signature so the priming-from-history step is observable.

The pairs are configured in [`pairs.yml`](./pairs.yml). The current set:

| pair id                                  | shared alertname              | base                                      | sibling                                     | what memory might break                              |
| ---------------------------------------- | ----------------------------- | ----------------------------------------- | ------------------------------------------- | ---------------------------------------------------- |
| `connection-pressure-real-vs-noisy`      | `RDSDatabaseConnectionsHigh`  | `002-connection-exhaustion`               | `007-connection-pressure-noisy-healthy`     | category divergence: real fault vs healthy           |
| `cpu-bad-query-vs-checkpoint-storm`      | `RDSCPUUtilizationHigh`       | `004-cpu-saturation-bad-query`            | `014-checkpoint-storm-cpu-saturation`       | sub-flavour divergence: bad-query vs checkpoint storm |
| `replication-lag-vs-cpu-redherring`      | `RDSReplicationLagHigh`       | `001-replication-lag`                     | `006-replication-lag-cpu-redherring`        | red-herring discipline weakened by prior clean diagnosis |

The strongest pair is `connection-pressure-real-vs-noisy` because the two
scenarios resolve to different `root_cause_category` values
(`resource_exhaustion` vs `healthy`). The other two pairs share a category
and exercise sub-flavour distinctions.

## How it runs today

```
python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory
```

Today this runs each `sibling` scenario without memory primed and emits a
`memory_mode=not_run` annotation per pair. That's the baseline correctness
check: every active pair should have its sibling pass on its own. If a
sibling fails baseline, the failure is not a memory-anchoring effect and
should be triaged before any memory work.

```
python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --json
python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --pair connection-pressure-real-vs-noisy
```

`--json` emits the full annotation list for downstream consumption.
`--pair` runs only the named pair.

## How it runs once Contextual Memory ships

When #1234 lands a memory layer, the runner gains memory mode:

```
python -m tests.synthetic.rds_postgres.axis_memory.run_axis_memory --memory
```

In memory mode the runner:

1. runs `base`, captures the investigation memory output
2. injects that memory into `sibling`'s investigation context
3. runs `sibling` and scores it
4. compares the memory-primed score to the baseline score
5. classifies the pair as `memory_helped`, `memory_hurt`, `memory_neutral`,
   or `pre_existing` (baseline already failed)

The classifier lives in [`scoring.py`](./scoring.py) and is already wired
in. The only piece waiting on #1234 is the actual memory-injection step
(`_run_with_memory` in `run_axis_memory.py`). It currently raises
`NotImplementedError` rather than silently degrading to baseline.

## Memory-mode classification

| mode             | baseline `sibling` | memory-primed `sibling` | reading                                                   |
| ---------------- | ------------------ | ----------------------- | --------------------------------------------------------- |
| `memory_neutral` | pass               | pass                    | safe; memory did not change the outcome                   |
| `memory_hurt`    | pass               | fail                    | regression caused by memory; flag before shipping memory  |
| `memory_helped`  | fail               | pass                    | memory recovered a baseline failure (uncommon, audit it)  |
| `pre_existing`   | fail               | fail                    | sibling fails on its own; not memory-attributable         |
| `not_run`        | any                | not exercised           | memory mode disabled (today's default)                    |

`memory_hurt` is the signal we care about. Any non-zero count there is a
candidate for blocking #1234 Phase 1 ship until the planner's anchoring is
mitigated.

## Adding a new pair

1. Identify two existing scenarios with the same `commonLabels.alertname`
   in their `alert.json` and different `root_cause_category` (or a different
   sub-flavour worth distinguishing).
2. Append a block to `pairs.yml` with `id`, `alert_signal`, `base`,
   `sibling`, `anchor_risk`, and `active: true`.
3. Run the runner; verify both scenarios pass baseline before shipping
   the pair as `active`.

If you want a pair on the books but not yet validated, set `active: false`
and the runner will skip it.

## What this scaffold deliberately does NOT do

- It does NOT inject memory. That's #1234's responsibility.
- It does NOT change the `run_suite.score_result(...)` scoring logic.
  The classifier compares pass/fail outcomes; it doesn't re-score.
- It does NOT add new fixture cases. Every pair reuses existing scenarios
  in the suite.
- It does NOT write to disk. All output is stdout.

## Pairing with #1234

When Phase 1 of Contextual Memory lands, the expected workflow is:

1. Implement the memory injection step in `_run_with_memory`.
2. Run `--memory` against the active pairs.
3. Read the memory-mode tally. Block ship on any `memory_hurt`.
4. If pre-existing failures appear, treat them as separate triage; they
   are not memory-attributable and should not gate the memory feature.

[issue-1234]: https://github.com/Tracer-Cloud/opensre/issues/1234
