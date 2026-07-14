variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all created resource names"
  type        = string
  default     = "opensre"
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
