variable "region" {
  description = "AWS region. Pinned in the bench pre-registration; do not change between runs of the same study."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Resource name prefix and tag value. Keep stable across applies."
  type        = string
  default     = "opensre-bench"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to assume the bench role via OIDC."
  type        = string
  default     = "Tracer-Cloud/opensre"
}

variable "results_bucket_name" {
  description = "S3 bucket name for per-run bench artifacts. Must be globally unique."
  type        = string
  default     = "tracer-cloud-bench-results"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the bench task. 30 days is enough for post-run investigation; bench artifacts of record live in S3."
  type        = number
  default     = 30
}

variable "task_cpu" {
  description = "Fargate task vCPU units. 2048 = 2 vCPU. Bench is API-bound (waits on LLM responses), not CPU-bound — 2 vCPU is sufficient for parallel async dispatch and roughly halves the per-run Fargate cost vs 4 vCPU. Bump to 4096 if you observe sustained 100% CPU in CloudWatch metrics."
  type        = string
  default     = "2048"
}

variable "task_memory" {
  description = "Fargate task memory in MiB. 4096 = 4 GiB. State Snapshot data (~500 MB corpus on disk) + per-cell async state fits comfortably in 4 GB. Bump to 8192 if OOMKilled errors appear in CloudWatch."
  type        = string
  default     = "4096"
}

variable "corpus_bucket_name" {
  description = "S3 bucket holding the Cloud-OpsBench corpus mirror. The Fargate task entrypoint pulls from s3://<this>/<corpus_hf_revision>/ at startup instead of downloading from Hugging Face (which is slow and rate-limited)."
  type        = string
  default     = "cloud-ops-bench-dataset"
}

variable "corpus_hf_revision" {
  description = "Hugging Face commit SHA pinned for this run. Must match a prefix that exists in s3://<corpus_bucket_name>/, populated by `make mirror-cloudopsbench-s3`. provenance.json records the same value so the artifact and the corpus are reproducibly paired. The default SHA must be present in S3 before the first Fargate run — see tests/benchmarks/AWS_BENCH_SETUP.md."
  type        = string
  default     = "ce0ded4f196f01e176cf1d69ec15c2db42b2a677"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.corpus_hf_revision))
    error_message = "corpus_hf_revision must be a 40-character lowercase hex SHA (HF git-style commit), e.g. ce0ded4f196f01e176cf1d69ec15c2db42b2a677."
  }
}

variable "image_tag" {
  description = <<-EOT
    Container image tag to run. ECR is configured with IMMUTABLE tag
    mutability, so a tag pushed once cannot be overwritten — every
    image push must use a unique tag (semver, git SHA, or build ID),
    and each Terraform apply explicitly chooses which tag to deploy.

    Default 'bootstrap' is a placeholder so `terraform apply` succeeds
    before the bench framework Dockerfile lands. Once images are being
    pushed, override per apply: `terraform apply -var=image_tag=<tag>`,
    or pin a value in terraform.tfvars / the pre-registration YAML.
  EOT
  type        = string
  default     = "bootstrap"
}
