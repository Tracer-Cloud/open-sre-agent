output "cluster_name" {
  description = "Shared ECS cluster running this team's services"
  value       = data.aws_ecs_cluster.shared.cluster_name
}

output "web_service_name" {
  description = "ECS service serving the web API"
  value       = aws_ecs_service.web.name
}

output "gateway_service_name" {
  description = "ECS service running the Slack Socket Mode gateway"
  value       = aws_ecs_service.gateway.name
}

output "web_log_group" {
  description = "CloudWatch log group for the web service"
  value       = aws_cloudwatch_log_group.web.name
}

output "gateway_log_group" {
  description = "CloudWatch log group for the Slack gateway service"
  value       = aws_cloudwatch_log_group.gateway.name
}

output "web_endpoint" {
  description = "HTTPS endpoint for the web API (null without certificate_arn; point your DNS record here)"
  value       = length(aws_lb.web) > 0 ? "https://${aws_lb.web[0].dns_name}" : null
}

output "memories_bucket" {
  description = "Per-team S3 bucket backing the persistent agent memory filesystem"
  value       = aws_s3_bucket.memories.bucket
}

output "memories_file_system_arn" {
  description = "ARN of the S3 Files filesystem mounted at /workspace/memories in every task"
  value       = aws_s3files_file_system.memories.arn
}
