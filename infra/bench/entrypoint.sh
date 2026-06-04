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

# Bench CLI invocation has two valid shapes:
#
#   1. ECS RunTask (production path) — the workflow injects BENCH_CONFIG +
#      BENCH_DEV_FLAG as containerOverrides env vars. The Dockerfile CMD
#      ["--help"] is left in place, so "$@" would be just "--help" — not
#      what we want. We construct the real invocation from the env vars.
#
#   2. Local `docker run` (developer path) — the operator passes
#      positional args directly. BENCH_CONFIG is unset, so we fall
#      through to "$@".
if [ -n "${BENCH_CONFIG:-}" ]; then
  echo "→ Invoking bench CLI from env: cli run ${BENCH_CONFIG} ${BENCH_DEV_FLAG:-}"
  # BENCH_DEV_FLAG is either '--dev' or '' — left unquoted so empty value
  # expands to no arg at all (vs an empty-string positional).
  set -- run "$BENCH_CONFIG" ${BENCH_DEV_FLAG:-}
else
  echo "→ Invoking bench CLI from CMD/args: python -m tests.benchmarks._framework.cli $*"
fi

# Run the bench in-process (not via exec) so we can sync artifacts AFTER it
# finishes — they live on the container's local fs at .bench-results/ and
# would otherwise vanish when the task exits. Temporarily disable -e so a
# non-zero bench exit code doesn't skip the upload step (we want artifacts
# from failed runs too).
set +e
python -m tests.benchmarks._framework.cli "$@"
BENCH_EXIT=$?
set -e

# Persist artifacts to S3 so report.json + provenance.json + cases/*.json
# survive container exit. BENCH_RESULTS_BUCKET is set by the ECS task
# definition. Skipped (with a warning) for local `docker run` where the
# operator can just look at the container fs.
SYNC_EXIT=0
if [ -d ".bench-results" ] && [ -n "${BENCH_RESULTS_BUCKET:-}" ]; then
  S3_DEST="s3://${BENCH_RESULTS_BUCKET}/runs/"
  ARTIFACT_COUNT=$(find .bench-results -type f | wc -l | tr -d ' ')
  echo "→ Syncing ${ARTIFACT_COUNT} artifact(s) from .bench-results/ to ${S3_DEST}"
  if aws s3 sync ".bench-results/" "$S3_DEST" \
        --no-progress \
        --region "${AWS_REGION:-us-east-1}"; then
    echo "→ Artifacts persisted to ${S3_DEST}"
  else
    echo "::error::S3 sync failed — artifacts were NOT persisted." >&2
    SYNC_EXIT=2
  fi
elif [ -d ".bench-results" ]; then
  echo "::warning::BENCH_RESULTS_BUCKET not set — artifacts stay in container fs only." >&2
fi

# Bench failure dominates the exit code; surface sync failure only when the
# bench itself succeeded (otherwise the operator sees the real cause first).
if [ "$BENCH_EXIT" -ne 0 ]; then
  exit "$BENCH_EXIT"
fi
exit "$SYNC_EXIT"
