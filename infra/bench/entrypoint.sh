#!/usr/bin/env bash
# Bench Fargate task entrypoint — pull the corpus from S3 then exec the CLI.
#
# Why this exists: the bench Docker image deliberately does NOT bake the
# Cloud-OpsBench corpus into the image. Coupling image rebuilds to dataset
# revisions would slow every push and bloat the image by ~3 GB. Instead we
# mirror the corpus into S3 once per HF revision and pull it at task
# startup — fast (~30 s same-region S3 sync vs ~10 min HF rate-limited
# download), no HF_TOKEN needed at runtime, and the revision pin in
# BENCH_CORPUS_HF_REVISION matches what provenance.json records for
# reproducibility.
#
# Required env (set by the ECS task definition):
#   BENCH_CORPUS_S3_BUCKET   — bucket holding the mirror
#   BENCH_CORPUS_HF_REVISION — HF commit SHA used as the S3 path prefix

set -euo pipefail

CORPUS_BUCKET="${BENCH_CORPUS_S3_BUCKET:-}"
CORPUS_REV="${BENCH_CORPUS_HF_REVISION:-}"

if [ -z "$CORPUS_BUCKET" ] || [ -z "$CORPUS_REV" ]; then
  echo "FATAL: BENCH_CORPUS_S3_BUCKET and BENCH_CORPUS_HF_REVISION must be set in the task definition." >&2
  exit 1
fi

# The CloudOpsBench adapter reads from tests/benchmarks/cloudopsbench/benchmark/
# (relative to the working dir baked into the image).
CORPUS_DEST="tests/benchmarks/cloudopsbench/benchmark"
mkdir -p "$CORPUS_DEST"

echo "→ Pulling corpus from s3://${CORPUS_BUCKET}/${CORPUS_REV}/ to ${CORPUS_DEST}"
START=$(date +%s)
aws s3 sync \
  "s3://${CORPUS_BUCKET}/${CORPUS_REV}/" \
  "$CORPUS_DEST" \
  --no-progress \
  --region "${AWS_REGION:-us-east-1}"
END=$(date +%s)

# `aws s3 sync` exits 0 even when the source prefix is empty or absent —
# without an explicit count check, the bench CLI would then start against
# an empty corpus and fail with a confusing "case not found" downstream
# error. Fail loudly here with a precise diagnostic instead.
CORPUS_FILE_COUNT=$(find "$CORPUS_DEST" -type f | wc -l | tr -d ' ')
echo "→ Corpus ready in $((END - START))s (${CORPUS_FILE_COUNT} files)"

if [ "$CORPUS_FILE_COUNT" -eq 0 ]; then
  echo "FATAL: s3://${CORPUS_BUCKET}/${CORPUS_REV}/ contained no files." >&2
  echo "Run \`HF_TOKEN=... make mirror-cloudopsbench-s3\` from a developer machine" >&2
  echo "with BENCH_S3_BUCKET=${CORPUS_BUCKET} to seed this revision." >&2
  exit 1
fi

echo "→ Invoking bench CLI: python -m tests.benchmarks._framework.cli $*"
exec python -m tests.benchmarks._framework.cli "$@"
