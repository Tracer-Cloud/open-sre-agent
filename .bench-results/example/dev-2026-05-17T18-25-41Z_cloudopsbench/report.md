# Benchmark Run — dev-2026-05-17T18-25-41Z_cloudopsbench

_config hash:_ `1c858d67a6e5a18d`  ·  _opensre SHA:_ `eab7777c-dirty`

**Started:** 2026-05-17T18:25:41.233533+00:00  
**Ended:** 2026-05-17T18:32:32.635837+00:00  
**Cost:** $0.0000 of $50.00 budget (0 calls, 0 in / 0 out)

## Conflict-of-interest disclosure

Conflict-of-interest disclosure: this benchmark run was authored, executed, and interpreted by the same person who builds opensre. Per the framework's integrity discipline, this structural bias is mitigated by (a) pre-registration committed before the run, (b) per-stratum reporting, (c) required negative-results section, (d) external replication of at least one cell before any public claim, (e) standardization-by-pinning of every parameter that affects results. Reviewers are encouraged to reproduce any cell independently.

## Headline (medians across all cases)

| LLM | n | a1 | a3 | tcr | cov | steps | iac | citation_grounding_rate | entity_existence_rate | kubectl_actionability_rate |
|---|---|---|---|---|---|---|---|---|---|---|
| `claude-default` | 15 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 |

## Per-stratum × per-LLM (medians)

### all

| mode/llm | a1 | a3 | partial_a1 | partial_a3 | tcr | exact | in_order | any_order | rel | cov | iac | rar | ztdr | citation_grounding_rate | entity_existence_rate | kubectl_actionability_rate | steps | mtti |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `opensre+llm/claude-default` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 |

### seen-shape

| mode/llm | a1 | a3 | partial_a1 | partial_a3 | tcr | exact | in_order | any_order | rel | cov | iac | rar | ztdr | citation_grounding_rate | entity_existence_rate | kubectl_actionability_rate | steps | mtti |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `opensre+llm/claude-default` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 |

## Negative results — where opensre lost or tied

```
opensre lost or tied on 15 of 15 cell(s) (adapter=cloudopsbench):
  - boutique/startup/31  mode=opensre+llm  llm=claude-default  run=0  a1=0.00  artifact=boutique_startup_31__opensre+llm__claude-default__0.json
  - boutique/startup/31  mode=opensre+llm  llm=claude-default  run=1  a1=0.00  artifact=boutique_startup_31__opensre+llm__claude-default__1.json
  - boutique/startup/31  mode=opensre+llm  llm=claude-default  run=2  a1=0.00  artifact=boutique_startup_31__opensre+llm__claude-default__2.json
  - trainticket/runtime/22  mode=opensre+llm  llm=claude-default  run=0  a1=0.00  artifact=trainticket_runtime_22__opensre+llm__claude-default__0.json
  - trainticket/runtime/22  mode=opensre+llm  llm=claude-default  run=1  a1=0.00  artifact=trainticket_runtime_22__opensre+llm__claude-default__1.json
  - trainticket/runtime/22  mode=opensre+llm  llm=claude-default  run=2  a1=0.00  artifact=trainticket_runtime_22__opensre+llm__claude-default__2.json
  - trainticket/runtime/34  mode=opensre+llm  llm=claude-default  run=0  a1=0.00  artifact=trainticket_runtime_34__opensre+llm__claude-default__0.json
  - trainticket/runtime/34  mode=opensre+llm  llm=claude-default  run=1  a1=0.00  artifact=trainticket_runtime_34__opensre+llm__claude-default__1.json
  - trainticket/runtime/34  mode=opensre+llm  llm=claude-default  run=2  a1=0.00  artifact=trainticket_runtime_34__opensre+llm__claude-default__2.json
  - trainticket/startup/14  mode=opensre+llm  llm=claude-default  run=0  a1=0.00  artifact=trainticket_startup_14__opensre+llm__claude-default__0.json
  - trainticket/startup/14  mode=opensre+llm  llm=claude-default  run=1  a1=0.00  artifact=trainticket_startup_14__opensre+llm__claude-default__1.json
  - trainticket/startup/14  mode=opensre+llm  llm=claude-default  run=2  a1=0.00  artifact=trainticket_startup_14__opensre+llm__claude-default__2.json
  - trainticket/runtime/92  mode=opensre+llm  llm=claude-default  run=0  a1=0.00  artifact=trainticket_runtime_92__opensre+llm__claude-default__0.json
  - trainticket/runtime/92  mode=opensre+llm  llm=claude-default  run=1  a1=0.00  artifact=trainticket_runtime_92__opensre+llm__claude-default__1.json
  - trainticket/runtime/92  mode=opensre+llm  llm=claude-default  run=2  a1=0.00  artifact=trainticket_runtime_92__opensre+llm__claude-default__2.json
```

## Pre-registration

`tests/benchmarks/configs/preregistrations/example.yml` (committed before run; expected deltas were locked in)

## Raw artifacts

Per-case JSON written to `.bench-results/example/dev-2026-05-17T18-25-41Z_cloudopsbench/cases` (15 files).

