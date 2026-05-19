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
