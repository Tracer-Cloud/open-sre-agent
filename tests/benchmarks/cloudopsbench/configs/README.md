# CloudOpsBench configs — layout

YAML configs that drive the bench framework. Each config names the
adapter (`benchmark: cloudopsbench`), the LLMs, the arms, the case
slice, and the budget. The framework reads the config, validates it
against the registered adapter's capabilities, and runs.

## Layout

```
configs/
├── README.md                                # this file
├── experiments/                             # configs grouped by experiment
│   └── exp_db_evidence_pipeline/            # one folder per experiment
│       ├── README.md                        # experiment summary + history
│       ├── preregistration.yml              # locked predictions / decision matrix
│       ├── smoke_db_pod_logs.yml            # the first smoke
│       ├── smoke_perf_only.yml              # diagnostic smoke
│       └── full_n.yml                       # the promotable full-N
├── preregistrations/                        # standalone pre-regs
│   ├── cloudopsbench_smoke.yml              # generic smoke pre-reg (placeholder for exploratory runs)
│   ├── cloudopsbench_v1.yml                 # baseline (cycle 1) pre-reg
│   └── exp_structured_outputs_v1.yml        # legacy — to migrate into experiments/
└── (flat .yml files)                        # historical / baseline configs (see below)
```

## Per-experiment folders are the source of truth going forward

For any new experiment:

1. Create `configs/experiments/exp_<name>/`.
2. Put the pre-registration in that folder as `preregistration.yml`.
3. Put the run configs (smoke, full-N, ablations) alongside.
4. Add a `README.md` that includes:
   - **What the experiment tests** — mechanisms by ID (M1, M2, …).
   - **Locked decision matrix** — what counts as ship / roll-back.
   - **Tracking issue** link.
   - **Run ledger** — a table mapping each run to (a) the config that
     produced it, (b) the image SHA, (c) the S3 run dir with raw
     artifacts, (d) the headline metrics, (e) the report doc. Every
     new dispatch adds a row. See
     [`experiments/exp_db_evidence_pipeline/README.md`](experiments/exp_db_evidence_pipeline/README.md)
     for the canonical shape.

The benefits:

- The file tree shows which configs belong together.
- `rm -rf exp_<name>/` cleanly removes the experiment + all metadata.
- A reviewer reads the folder's README + the pre-reg without hunting
  through `preregistrations/` for the matching file.
- Pre-reg + run configs cannot drift in opposite directions if they
  live in the same folder reviewed in the same PR.
- Config ↔ result mapping is explicit. Anyone asking "where did this
  number come from?" follows the link from the report → the S3 run
  dir → the config + image SHA → the pre-reg in the same folder.

## What's still in the flat directory

Older configs from the first ~6 weeks of bench work are still flat at
`configs/*.yml`. They map to historical experiments (some rejected, some
baseline) per
[`~/DevBox/tracer-cloud/opensre-notes/cloudopsbench-experiments-chain.md`](../../../../opensre-notes/cloudopsbench-experiments-chain.md).
Migrating each into an `experiments/exp_<name>/` folder is a lazy
follow-up — not blocking. Mapping below for future reference:

| Flat config | Likely experiment folder |
|---|---|
| `cloudopsbench_definitive_openai.yml`, `cloudopsbench_definitive_floor0_openai.yml`, `cloudopsbench_definitive_trimmed_prompt_openai.yml` | `exp_definitive_baseline` |
| `cloudopsbench_floorsweep_openai.yml`, `cloudopsbench_floor0_ablation_openai.yml`, `cloudopsbench_floor_ablation_v2_openai.yml` | `exp_floor_ablation` |
| `cloudopsbench_fixa_validation_openai.yml` | `exp_fixa_validation` (rejected) |
| `cloudopsbench_vocabpilot_openai.yml`, `cloudopsbench_vocabpilot_anthropic.yml` | `exp_vocabpilot` |
| `cloudopsbench_trimmed_prompt_openai.yml` | `exp_trimmed_prompt` |
| `cloudopsbench_structured_outputs_smoke_openai.yml`, `cloudopsbench_structured_outputs_smoke_100_openai.yml`, `cloudopsbench_structured_outputs_openai.yml` | `exp_structured_outputs` (rejected; pre-reg already exists) |
| `cloudopsbench_v1.yml`, `cloudopsbench_v1_openai.yml`, `cloudopsbench_v1_anthropic.yml`, `cloudopsbench_v1_deepseek.yml` | baselines (keep flat — they're the canonical cross-LLM baseline) |
| `cloudopsbench_smoke.yml` | baseline smoke (keep flat) |
| `cloudopsbench_control_openai.yml`, `cloudopsbench_postpatch_anthropic.yml` | one-off controls (archive candidate) |

## Naming conventions inside `experiments/exp_<name>/`

| Suffix | Meaning |
|---|---|
| `preregistration.yml` | The locked pre-reg (exactly one per experiment). |
| `smoke_<focus>.yml` | A small-N exploratory run (≤ 100 cases). The `<focus>` describes the slice (e.g. `perf_only`, `db_pod_logs`). |
| `full_n.yml` | The full-corpus run (n=452 for CloudOpsBench v1). Promotable. |
| `ablation_<arm>.yml` | An ablation that drops one mechanism. The `<arm>` names what was dropped (e.g. `layer1_only`). |

## What a config must contain

Mandatory fields (per `BenchmarkConfig` in
`tests/benchmarks/_framework/config.py`):

- `benchmark` — adapter name; resolved via the framework registry.
- `modes`, `llms`, `model_versions`, `runs_per_case`, `cost_budget_usd`, `seed`.
- `pre_registration_path` — relative path from repo root. For
  experiments in this layout, point at `./preregistration.yml`.
- `output_dir`, `filters`, `report_formats`.

Adapter-specific knobs (`agent_variant`, `predictor_variant`,
`min_tool_calls`) are validated against the adapter's
`AdapterCapabilities`. A capability the adapter doesn't declare is
refused at lint time.
