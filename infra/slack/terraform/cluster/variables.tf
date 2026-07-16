variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Name of the shared ECS cluster. The per-team stack looks this up by name."
  type        = string
  default     = "opensre-shared"
}

variable "container_insights" {
  description = "CloudWatch Container Insights mode for the cluster (disabled keeps cost at zero)"
  type        = string
  default     = "disabled"

  validation {
    condition     = contains(["disabled", "enabled", "enhanced"], var.container_insights)
    error_message = "container_insights must be one of: disabled, enabled, enhanced."
  }
}
