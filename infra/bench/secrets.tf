# Secrets Manager entries for LLM provider keys + HF token.
#
# Terraform manages the secret RESOURCE (existence, naming, IAM access).
# Terraform does NOT manage the secret VALUE — values are seeded out-of-band
# after `terraform apply`:
#
#   aws secretsmanager put-secret-value \
#     --secret-id opensre-bench/llm/anthropic_api_key \
#     --secret-string "$ANTHROPIC_API_KEY"
#
# Values must NEVER appear in Terraform code, tfvars, plan output, or state.
# Rotation is manual (external SaaS keys have no programmatic rotation).

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${local.name_prefix}/llm/anthropic_api_key"
  description = "Anthropic API key for Cloud-OpsBench runs. Value seeded out-of-band."

  # 30-day recovery window is safer than immediate delete; reduces blast radius
  # of an accidental destroy. Set to 0 only in throwaway dev accounts.
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret" "openai_api_key" {
  name        = "${local.name_prefix}/llm/openai_api_key"
  description = "OpenAI API key for Cloud-OpsBench runs (gpt-4o, gpt-5). Value seeded out-of-band."

  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret" "deepseek_api_key" {
  name        = "${local.name_prefix}/llm/deepseek_api_key"
  description = "DeepSeek API key for Cloud-OpsBench runs (deepseek-v3.2). DeepSeek uses an OpenAI-compatible API; base URL configured at runtime, not here. Value seeded out-of-band."

  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret" "hf_token" {
  name        = "${local.name_prefix}/llm/hf_token"
  description = "Hugging Face token for State Snapshot dataset download (tracer-cloud/cloud-ops-bench-dataset)."

  recovery_window_in_days = 30
}
