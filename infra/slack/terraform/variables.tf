variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all created resource names. Convention: opensre-<env>-<team> (e.g. opensre-dev-dogfood)."
  type        = string
  default     = "opensre"
}

variable "cluster_name" {
  description = "Name of the shared ECS cluster to run this team's services on (created by ../cluster)"
  type        = string
  default     = "opensre-shared"
}

variable "team" {
  description = "Team identifier. Part of the per-team memory bucket (opensre-memories-<env>-<team>) and tags all resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.team))
    error_message = "team must be lowercase letters, digits, and hyphens (used in an S3 bucket name)."
  }
}

variable "env" {
  description = "Environment name. Part of the memory bucket name (opensre-memories-<env>-<team>) and cost-allocation tags (e.g. dev, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.env))
    error_message = "env must be lowercase letters, digits, and hyphens (used in an S3 bucket name)."
  }
}

variable "image_uri" {
  description = "Full ECR image URI (from `make build-image`), e.g. <account>.dkr.ecr.<region>.amazonaws.com/opensre:latest"
  type        = string
}

variable "web_port" {
  description = "Port the web container listens on"
  type        = number
  default     = 8000
}

variable "web_ingress_cidr" {
  description = "CIDR allowed to reach the web API port"
  type        = string
  default     = "0.0.0.0/0"
}

variable "task_cpu" {
  description = "Fargate task CPU units per service"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory (MiB) per service"
  type        = number
  default     = 1024
}

variable "llm_provider" {
  description = "Default LLM provider name (LLM_PROVIDER)"
  type        = string
  default     = ""
}

variable "anthropic_api_key" {
  description = "Anthropic API key"
  type        = string
  default     = ""
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  default     = ""
  sensitive   = true
}

variable "slack_bot_token" {
  description = "Slack bot token (xoxb-…)"
  type        = string
  sensitive   = true
}

variable "slack_app_token" {
  description = "Slack app-level token for Socket Mode (xapp-…)"
  type        = string
  sensitive   = true
}

variable "slack_allowed_users" {
  description = "Comma-separated Slack user IDs allowed to talk to the bot. Required unless slack_allow_open_workspace is true."
  type        = string
  default     = ""
}

variable "slack_allow_open_workspace" {
  description = "If true, sets SLACK_ALLOW_OPEN_WORKSPACE=1 (any workspace member may talk to the bot). Dogfood only — prefer slack_allowed_users."
  type        = bool
  default     = false
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL for outbound findings delivery (SLACK_WEBHOOK_URL); empty disables webhook delivery"
  type        = string
  default     = ""
  sensitive   = true
}

variable "alert_listener_token" {
  description = "Bearer token required by /alerts and /investigate (OPENSRE_ALERT_LISTENER_TOKEN); empty leaves those routes loopback-only"
  type        = string
  default     = ""
  sensitive   = true
}

variable "database_url" {
  description = "Postgres DSN for the investigations store (DATABASE_URL); empty uses the in-memory store"
  type        = string
  default     = ""
  sensitive   = true
}

variable "artifacts_bucket" {
  description = "S3 bucket name for investigation report artifacts; empty keeps reports local-only"
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate ARN. When set, an ALB terminates HTTPS in front of the web service and direct container access is closed"
  type        = string
  default     = ""
}

# --- Agent memory (S3 Files) ------------------------------------------------

variable "memories_noncurrent_version_expiration_days" {
  description = "Days after which noncurrent memory-bucket object versions expire (append-heavy JSONL churns versions)"
  type        = number
  default     = 30
}

variable "memories_uid" {
  description = "POSIX UID the access point forces for all memory-filesystem ops (overrides the container user, so ownership is stable across restarts regardless of how the container runs)"
  type        = number
  default     = 1000
}

variable "memories_gid" {
  description = "POSIX GID the access point pins for the mounted memory filesystem"
  type        = number
  default     = 1000
}

variable "gateway_heartbeat_path" {
  description = "Path the gateway writes its liveness heartbeat to and the health check reads. Injected into the container as SLACK_GATEWAY_HEARTBEAT_PATH so both sides share one value. Must sit on writable ephemeral disk, not the memory mount."
  type        = string
  default     = "/workspace/scratch/gateway.heartbeat"
}

variable "gateway_heartbeat_stale_seconds" {
  description = "Seconds without a gateway heartbeat refresh before the container health check marks the task unhealthy (worker refreshes every 15s while connected)"
  type        = number
  default     = 90
}
