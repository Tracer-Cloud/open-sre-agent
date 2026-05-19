# CloudOpsBench

CloudOpsBench runner code lives here, but the benchmark corpus is downloaded from
Hugging Face instead of being checked into this repository.

Download the dataset:

```bash
make download-cloudopsbench-hf
```

By default this downloads `benchmark/**` from
`tracer-cloud/cloud-ops-bench-dataset` into `tests/benchmarks/cloudopsbench/`.

Validate the downloaded corpus:

```bash
make validate-cloudopsbench
```

Run the benchmark:

```bash
make test-cloudopsbench
```

Run the reusable benchmark harness:

```bash
uv run opensre bench validate tests/benchmarks/configs/claude-vs-paper.yml
uv run opensre bench run --config tests/benchmarks/configs/claude-vs-paper.yml
```

The harness writes JSON, Markdown, and optional HTML reports under the config's
`output_dir`, including per-case decision traces, estimated token cost when
token usage is present in the run state, CloudOpsBench paper-metric scores, and
the checked-in paper A@1 comparison for GPT-4o, GPT-5, Claude-4-Sonnet, and
DeepSeek-V3.2. Use `workers: 1` in YAML for serial execution; configs default to
parallel execution.

The executable mode is `opensre+llm`. The LLM-alone column comes from the
published Cloud-OpsBench paper baselines and is added during report rendering;
the harness intentionally does not relabel an OpenSRE run as an LLM-alone run.

Run only a subset of cases:

```bash
make test-cloudopsbench CLOUDOPSBENCH_LIMIT=10
```

You can combine the limit with the existing filters:

```bash
make test-cloudopsbench SYSTEM=boutique FAULT=service CLOUDOPSBENCH_LIMIT=5
```

Override the source repo or local directory when needed:

```bash
make download-cloudopsbench-hf \
  CLOUDOPSBENCH_HF_DATASET_ID=tracer-cloud/cloud-ops-bench-dataset \
  CLOUDOPSBENCH_DATASET_DIR=/tmp/cloudopsbench

make test-cloudopsbench CLOUDOPSBENCH_BENCHMARK_DIR=/tmp/cloudopsbench/benchmark
```
