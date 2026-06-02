# Cache the CloudOpsBench corpus in S3 (design notes)

> **Status:** implemented. `entrypoint.sh`, the Dockerfile changes,
> `infra/bench/{fargate,iam_task,variables}.tf`, and the
> `make mirror-cloudopsbench-s3` target all ship in the same PR.
> See [AWS_BENCH_SETUP.md](AWS_BENCH_SETUP.md) for the one-time bootstrap
> steps before the first Fargate run.
>
> This document is kept as the design rationale — useful for reviewers
> trying to understand the trade-off vs baking the corpus into the image
> or downloading from Hugging Face at runtime.

## Problem

Each ECS task currently has to fetch ~few hundred MB of CloudOpsBench
data from Hugging Face at startup, because:

1. `infra/bench/Dockerfile.bench` does NOT bake the dataset into the
   image (and shouldn't — that would couple image rebuilds to dataset
   revisions and add ~500 MB to every push).
2. The container's `ENTRYPOINT` jumps straight into the CLI with no
   download step, so the corpus has to be present at runtime.
3. Anonymous HF Hub access caps at ~60 requests/min — the existing
   `make download-cloudopsbench-hf` for the 1,000+ file corpus hits HTTP
   429 rate limits about a third of the way through and falls into
   232-second backoff retries.

Setting `HF_TOKEN` (the quick fix already shipped in the Makefile)
raises the cap to ~1000 req/min and should kill the 429s, but it still
makes every Fargate task wait several minutes for the same data and
ties the run to HF availability + ongoing rate-limit policy.

## Proposal: mirror the corpus to the existing bench S3 bucket

The bench Terraform already provisions an S3 bucket for run artifacts
(see [`infra/bench/`](../../infra/bench/)). Add a `corpus/` prefix and
keep one immutable copy per HF revision.

```
s3://opensre-bench/
├── corpus/
│   └── cloudopsbench/
│       └── <hf-revision-sha>/
│           ├── boutique/
│           │   ├── admission/
│           │   ├── infrastructure/
│           │   ├── performance/
│           │   ├── runtime/
│           │   ├── scheduling/
│           │   ├── service-routing/
│           │   └── startup/
│           └── README.md
└── runs/
    └── <date>-<sha>/
        ├── report.json
        ├── report.md
        ├── report.html
        ├── provenance.json
        └── cases/*.json
```

The HF-revision-keyed prefix matches what `provenance.json` already
records (`dataset.hf_revision`), so a published report literally
points at the immutable corpus snapshot it used.

## Architecture

### A. One-time bootstrap (run from a laptop or as a workflow)

```bash
# Pull from HF with HF_TOKEN, then push to S3 at the revision-keyed prefix.
make download-cloudopsbench-hf
HF_REV=$(huggingface-cli scan-cache | grep cloud-ops-bench-dataset | awk '{print $NF}')
aws s3 sync \
    tests/benchmarks/cloudopsbench/benchmark/ \
    "s3://opensre-bench/corpus/cloudopsbench/${HF_REV}/" \
    --no-progress
```

Run this:
- Once at setup
- Again whenever the upstream HF dataset is revised (rarely)

Could become a workflow `benchmark-corpus-mirror.yml` triggered manually
or on a monthly schedule. ~30 lines, mirrors the
`benchmark-seed-secret.yml` pattern (OIDC role + aws CLI).

### B. Container entrypoint change

Replace the direct CLI entrypoint with a small bash wrapper that pulls
the corpus from S3 first, then invokes the CLI:

```dockerfile
# infra/bench/Dockerfile.bench (sketch)
COPY infra/bench/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

```bash
#!/usr/bin/env bash
# infra/bench/entrypoint.sh
set -euo pipefail

CORPUS_REV="${BENCH_CORPUS_HF_REVISION:-}"
if [ -z "$CORPUS_REV" ]; then
  echo "BENCH_CORPUS_HF_REVISION env var is required (set by task definition)." >&2
  exit 1
fi

CORPUS_DEST="tests/benchmarks/cloudopsbench/benchmark"
mkdir -p "$CORPUS_DEST"

echo "→ Pulling corpus from S3 at revision ${CORPUS_REV}"
aws s3 sync \
    "s3://opensre-bench/corpus/cloudopsbench/${CORPUS_REV}/" \
    "$CORPUS_DEST" \
    --no-progress

echo "→ Launching benchmark CLI"
exec python -m tests.benchmarks._framework.cli "$@"
```

### C. Task definition update

Add `BENCH_CORPUS_HF_REVISION` to the container's env in the Terraform
task definition. Pin to the exact HF revision so a re-run never silently
picks up a newer corpus.

## Benefits

| Property | Today (HF download at runtime) | After (S3 mirror) |
|---|---|---|
| Time to start a run | ~5-10 min, sometimes longer with 429s | ~30 s |
| Reproducibility | HF Hub availability dependency | Immutable per S3 revision prefix |
| Rate-limit risk | High under `HF_TOKEN`; severe without | None at this scale |
| HF_TOKEN required at task runtime | Yes | No |
| S3 monthly cost | $0 | ~$0.02 (500 MB Standard storage) |
| Image size | unchanged | unchanged |

## Migration plan

1. Bootstrap the S3 prefix from a laptop using the bash above (~5 min)
2. Update `Dockerfile.bench` to use the entrypoint wrapper
3. Rebuild + push the image via `benchmark-image.yml` with the new tag
4. Add `BENCH_CORPUS_HF_REVISION` to the Terraform task definition;
   apply it
5. Test: launch a bench run, confirm the corpus arrives in <1 min and the
   CLI sees all 452 cases
6. Remove the `make download-cloudopsbench-hf` step from any CI workflow
   that no longer needs it

## Open questions for the user

- **Should we keep multiple HF revisions in S3?** Storage is cheap;
  yes is the default, lets us reproduce older runs.
- **Should the bootstrap workflow run on a schedule (e.g., monthly) to
  catch upstream revisions automatically?** Or stay manual to preserve
  pre-registration's revision lock?
- **Is the bench S3 bucket fine to host the corpus, or should we add
  a separate `opensre-bench-corpus` bucket?** Same bucket means one
  IAM resource to grant; separate bucket means lifecycle policies can
  differ (corpus is permanent, runs/ probably expire after 90 days).
