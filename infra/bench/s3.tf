# Results bucket for bench artifacts.
#
# Layout per run:
#   s3://${var.results_bucket_name}/runs/<date>-<opensre_sha>/
#     ├── pre-registration.yml  (copy of pinned pre-reg)
#     ├── config.yml            (copy of run config)
#     ├── report.json           (aggregated metrics)
#     ├── report.md             (human-readable report)
#     └── cells/                (per-case JSON, one file per cell)
#
# Versioning is enabled so an accidental overwrite of a published run can be
# recovered. Encryption is AWS-managed KMS — sufficient for benchmark
# artifacts (no PII, no customer data).

resource "aws_s3_bucket" "results" {
  bucket = var.results_bucket_name
}

resource "aws_s3_bucket_versioning" "results" {
  bucket = aws_s3_bucket.results.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy — versioning is ON for safety (accidental overwrite of a
# published run can be recovered) but old versions otherwise accumulate
# forever. Two rules:
#
#   1. Expire noncurrent (overwritten / deleted) versions after 90 days.
#      The 90-day window is long enough for a real "oops we need to
#      restore" to be noticed; after that, the cost of keeping every
#      historical version outweighs the recovery value.
#
#   2. Abort incomplete multipart uploads after 1 day. Failed large
#      uploads leave orphaned multipart parts that are billed for
#      storage but invisible in the console.
#
# Current-version objects are NEVER expired — published bench artifacts
# stay forever, matching the pre-registration's reproducibility promise.
resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "expire-noncurrent-versions-after-90-days"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads-after-1-day"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Lifecycle configuration requires versioning to already be configured.
  depends_on = [aws_s3_bucket_versioning.results]
}
