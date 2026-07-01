# Execution role — assumed by the ECS agent, NOT by the container.
#
# Responsibilities that happen before the container starts and after it ends:
#   - Pull image from ECR
#   - Write container logs to CloudWatch
#   - Resolve `secrets` references in the task definition (fetch from
#     Secrets Manager and inject as env vars before the container starts)
#
# Distinct from the task role on purpose: container-time code never needs
# these permissions, and execution-time code never needs to write S3.

data "aws_iam_policy_document" "execution_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name_prefix}-execution"
  description        = "ECS execution role - image pull, log write, secret resolution."
  assume_role_policy = data.aws_iam_policy_document.execution_assume_role.json
}

# AWS-managed policy covers ECR pull + CloudWatch log writes for ECS tasks.
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Secret resolution at task start — ECS needs to fetch the secret values to
# inject them into the container's environment. Per-ARN scope.
data "aws_iam_policy_document" "execution_secrets_read" {
  statement {
    sid     = "ResolveTaskDefinitionSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
      aws_secretsmanager_secret.deepseek_api_key.arn,
      aws_secretsmanager_secret.hf_token.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets_read" {
  name   = "secrets-resolve"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets_read.json
}
