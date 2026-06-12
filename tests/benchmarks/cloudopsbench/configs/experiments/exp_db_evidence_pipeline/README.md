# exp_db_evidence_pipeline

DB-evidence pipeline experiment — tests two coordinated structural rules
in opensre's bench prompt:

1. **Dependency-traversal rule (Layer 1):** when the failing service
   shows connection-shaped errors, also query the stateful dependency
   pod's logs (MySQL / Redis / queue) before concluding.
2. **Alert-anchored upstream-attribution rule (Layer 2 / V2 prompt):**
   when the alert names a service AND describes a performance / latency
   / network / resource issue, trust the alert's named service. The
   downstream services with noisy logs are usually victims, not causes.

Both rules live in
`tests/benchmarks/cloudopsbench/bench_agent.py:_TRIMMED_BENCH_SYSTEM_PROMPT`.

## Files

| File | Purpose |
|---|---|
| `preregistration.yml` | Locked predictions + decision matrix + rollback rules. Committed before any full-N run. |
| `smoke_db_pod_logs.yml` | n=40 three-arm smoke; first observation of the dual-layer pipeline. |
| `smoke_perf_only.yml` | n=25 Performance-only diagnostic; isolates the Performance category to test for regression. |
| `full_n.yml` | n=452 full-N promotable run. Uses `preregistration.yml`. |

## Runs (config ↔ result ledger)

Every row links the **config that produced it**, the **S3 run dir** with
raw artifacts, and the **report** with the analysis. Keep this updated
when a new run lands.

| Started | Image | Config used | S3 run dir | Status | Headline | Report |
|---|---|---|---|---|---|---|
| 2026-06-11T11:02Z | `6a92295` | [`smoke_db_pod_logs.yml`](smoke_db_pod_logs.yml) | `runs/cloudopsbench_db_pod_logs_smoke_openai/dev-2026-06-11T11-02-51Z_cloudopsbench/` | Completed | Admission +33pp SIG · Performance −14.8pp ns · Aggregate ns | [exp_db_evidence_pipeline.md](../../../../../../opensre-notes/exp_db_evidence_pipeline.md) |
| 2026-06-11T12:56Z | `1ae5c83` | [`smoke_perf_only.yml`](smoke_perf_only.yml) (V1) | `runs/cloudopsbench_perf_only_smoke_openai/dev-2026-06-11T12-56-37Z_cloudopsbench/` | Completed | Performance −9.3pp ns | [exp_perf_only_smoke.md](../../../../../../opensre-notes/exp_perf_only_smoke.md) |
| 2026-06-11T16:08Z | `ec2c675` | [`smoke_perf_only.yml`](smoke_perf_only.yml) (V2) | `runs/cloudopsbench_perf_only_smoke_openai/dev-2026-06-11T16-08-59Z_cloudopsbench/` | Completed | Performance −6.7pp ns | (in exp_perf_only_smoke.md) |
| 2026-06-11T17:57Z | `65f69e0` | [`smoke_perf_only.yml`](smoke_perf_only.yml) (V3) | `runs/cloudopsbench_perf_only_smoke_openai/dev-2026-06-11T17-57-57Z_cloudopsbench/` | Completed (V3 rolled back) | Performance −13.3pp ns; worse than V1 | (in exp_perf_only_smoke.md) |
| 2026-06-11T21:03Z | `3d20513` | [`full_n.yml`](full_n.yml) | `runs/cloudopsbench_db_evidence_pipeline_full_openai/2026-06-11T21-03-03Z_cloudopsbench/` | Aborted at 66% (cost budget) | Aggregate +7.13pp SIG · Admission +41.5pp SIG · Unseen +18.8pp SIG — **NOT promotable** (partial + SHA gap) | [exp_db_evidence_pipeline_full_partial.md](../../../../../../opensre-notes/exp_db_evidence_pipeline_full_partial.md) |
| 2026-06-12T~ (in flight) | `c27bee9` | [`full_n.yml`](full_n.yml) | `runs/cloudopsbench_db_evidence_pipeline_full_openai/<pending>/` | Running | (pending) | (pending) |

S3 bucket: `s3://tracer-cloud-bench-results/`. To pull a run locally:

```bash
export AWS_PROFILE=tracer-cloud
aws s3 sync s3://tracer-cloud-bench-results/<S3 run dir>/ .bench-results/<S3 run dir>/
```

Full experiment chain (every cycle, including pre-experiment baselines and
rejected experiments not represented here):
[`~/DevBox/tracer-cloud/opensre-notes/cloudopsbench-experiments-chain.md`](../../../../../../opensre-notes/cloudopsbench-experiments-chain.md).

## Tracking issue

#2074 — CloudOpsBench beat-the-paper workstream.
