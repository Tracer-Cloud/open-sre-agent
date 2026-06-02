# Fargate cluster + task definition for the bench.
#
# The cluster holds task definitions; tasks are launched one-off via
# `aws ecs run-task` (from CI or developer machine). There is no ECS service
# here — the bench is a job, not a long-running service.
#
# Network: uses default VPC public subnets. The task gets a public IP and
# reaches LLM provider APIs via the internet gateway. No NAT, no private
# subnet — keeps cost low and simplifies the v1 setup. If/when the bench
# needs to live in a dedicated VPC, swap the subnets here.

# Default VPC discovery (good enough for v1).
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Security group: outbound only. Bench makes HTTPS calls to LLM APIs + S3 +
# Secrets Manager + ECR; no inbound traffic required.
resource "aws_security_group" "task" {
  name        = "${local.name_prefix}-task"
  description = "Bench Fargate task - outbound only."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound TCP/UDP - bench reaches LLM APIs and AWS service endpoints"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "bench" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "bench" {
  cluster_name       = aws_ecs_cluster.bench.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 100
  }
}

# Task definition.
#
# The container image is referenced by `:latest` for v0. Pin a specific
# image digest in pre-registration before publication runs so the exact
# image used can be reproduced.
#
# `secrets` injects API keys from Secrets Manager as env vars at task start —
# opensre's existing env-var-based LLM client picks them up unchanged.
#
# Logs go to CloudWatch. If/when the team wants logs in Grafana, configure
# Grafana Cloud's native CloudWatch data source (in Grafana's UI, not here).
resource "aws_ecs_task_definition" "bench" {
  family                   = local.name_prefix
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "bench"
      image     = "${aws_ecr_repository.bench.repository_url}:${var.image_tag}"
      essential = true

      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "BENCH_RESULTS_BUCKET", value = aws_s3_bucket.results.bucket },
        # entrypoint.sh reads these two and runs `aws s3 sync` before the CLI
        { name = "BENCH_CORPUS_S3_BUCKET", value = var.corpus_bucket_name },
        { name = "BENCH_CORPUS_HF_REVISION", value = var.corpus_hf_revision },
        { name = "DEEPSEEK_BASE_URL", value = "https://api.deepseek.com" },
      ]

      secrets = [
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn
        },
        {
          name      = "OPENAI_API_KEY"
          valueFrom = aws_secretsmanager_secret.openai_api_key.arn
        },
        {
          name      = "DEEPSEEK_API_KEY"
          valueFrom = aws_secretsmanager_secret.deepseek_api_key.arn
        },
        {
          name      = "HF_TOKEN"
          valueFrom = aws_secretsmanager_secret.hf_token.arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.bench.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "bench"
        }
      }
    }
  ])
}
