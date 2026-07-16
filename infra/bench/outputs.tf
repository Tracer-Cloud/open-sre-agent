output "ecr_repository_url" {
  description = "Push bench container images here. Used as the image base in task definition."
  value       = aws_ecr_repository.bench.repository_url
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN. Passed to `aws ecs run-task --cluster` when launching a bench."
  value       = aws_ecs_cluster.bench.arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name. More convenient than ARN for CLI use."
  value       = aws_ecs_cluster.bench.name
}

output "task_definition_family" {
  description = "Task definition family. Pass to `aws ecs run-task --task-definition`."
  value       = aws_ecs_task_definition.bench.family
}

output "task_definition_arn" {
  description = "Full ARN of the latest task definition revision. Pin in pre-registration to lock the exact bench task definition used for a study."
  value       = aws_ecs_task_definition.bench.arn
}

output "task_role_arn" {
  description = "Task role ARN - runtime permissions for the bench container."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "Execution role ARN - ECS-side permissions for image pull + secret resolution."
  value       = aws_iam_role.execution.arn
}

output "github_actions_role_arn" {
  description = "Runtime role: assumed by .github/workflows/bench.yml (RunTask) and bench-seed-deepseek.yml (PutSecretValue)."
  value       = aws_iam_role.github_actions.arn
}

output "terraform_plan_role_arn" {
  description = "Plan-only role: assumed by .github/workflows/terraform-bench.yml on PRs. ReadOnlyAccess + state-bucket read."
  value       = aws_iam_role.terraform_plan.arn
}

output "results_bucket_name" {
  description = "S3 bucket holding per-run artifacts."
  value       = aws_s3_bucket.results.bucket
}

output "log_group_name" {
  description = "CloudWatch log group for live task logs."
  value       = aws_cloudwatch_log_group.bench.name
}

output "security_group_id" {
  description = "Task security group. Pass to `aws ecs run-task --network-configuration`."
  value       = aws_security_group.task.id
}

output "subnet_ids" {
  description = "Subnets the task can be launched into. Pass to `aws ecs run-task --network-configuration`."
  value       = data.aws_subnets.default_public.ids
}

output "secret_arns" {
  description = "Map of secret name to ARN. Use for `aws secretsmanager put-secret-value --secret-id <arn>` when seeding."
  value = {
    anthropic_api_key = aws_secretsmanager_secret.anthropic_api_key.arn
    openai_api_key    = aws_secretsmanager_secret.openai_api_key.arn
    deepseek_api_key  = aws_secretsmanager_secret.deepseek_api_key.arn
    hf_token          = aws_secretsmanager_secret.hf_token.arn
  }
}
