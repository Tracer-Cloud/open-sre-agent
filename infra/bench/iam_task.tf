# Task role — assumed by the bench container at runtime.
#
# Grants:
#   - Read the four bench secrets (anthropic, openai, deepseek, hf_token)
#   - Read/write the results S3 bucket
#   - No other AWS access
#
# Per-secret ARN grants, not wildcards: any new secret requires an explicit
# Terraform diff. Same for S3 — bucket-scoped, not account-scoped.

data "aws_iam_policy_document" "task_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-task"
  description        = "Runtime role for the bench container - secrets read + results write."
  assume_role_policy = data.aws_iam_policy_document.task_assume_role.json
}

data "aws_iam_policy_document" "task_secrets_read" {
  statement {
    sid     = "ReadBenchLLMSecrets"
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

resource "aws_iam_role_policy" "task_secrets_read" {
  name   = "secrets-read"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_secrets_read.json
}

data "aws_iam_policy_document" "task_results_rw" {
  statement {
    sid    = "WriteRunArtifacts"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.results.arn}/*"]
  }

  statement {
    sid       = "ListResultsBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.results.arn]
  }
}

resource "aws_iam_role_policy" "task_results_rw" {
  name   = "results-rw"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_results_rw.json
}
