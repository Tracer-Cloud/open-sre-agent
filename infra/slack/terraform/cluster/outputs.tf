output "cluster_name" {
  description = "Name of the shared ECS cluster (pass to the per-team stack's cluster_name)"
  value       = aws_ecs_cluster.shared.name
}

output "cluster_arn" {
  description = "ARN of the shared ECS cluster"
  value       = aws_ecs_cluster.shared.arn
}
