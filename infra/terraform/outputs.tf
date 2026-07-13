output "cluster_name" {
  description = "ECS cluster running the OpenSRE services"
  value       = aws_ecs_cluster.this.name
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
