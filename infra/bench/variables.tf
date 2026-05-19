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
  description = "Fargate task vCPU units. 4096 = 4 vCPU. Bench is API-bound, not CPU-bound; this is sized for parallel async, not throughput."
  type        = string
  default     = "4096"
}

variable "task_memory" {
  description = "Fargate task memory in MiB. 8192 = 8 GiB. Headroom for State Snapshot data + per-cell async state."
  type        = string
  default     = "8192"
}
