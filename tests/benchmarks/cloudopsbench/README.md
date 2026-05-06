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
