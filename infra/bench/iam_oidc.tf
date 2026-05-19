# GitHub Actions OIDC trust — lets CI assume an AWS role without long-lived
# access keys. The role grants only the operations needed to launch the bench
# task and fetch its results.
#
# Trust policy is scoped to var.github_repository. Tighten the `sub` condition
# below if you want to restrict by branch / environment (recommended for
# production runs). For v1 we accept any ref/branch from the repo.

# OIDC provider — one per AWS account. If it already exists, import it:
#   terraform import aws_iam_openid_connect_provider.github \
#     arn:aws:iam::<acct>:oidc-provider/token.actions.githubusercontent.com
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name_prefix}-github-actions"
  description        = "Assumed by GitHub Actions in ${var.github_repository} to launch bench tasks."
  assume_role_policy = data.aws_iam_policy_document.github_actions_trust.json
}

# Permissions the CI workflow needs:
#   - Launch the bench Fargate task
#   - Read its status (poll until done)
#   - Pass the task + execution roles to ECS (RunTask requires PassRole)
#   - Read results from S3 (artifact upload)
#   - Read CloudWatch logs (tail during run)
data "aws_iam_policy_document" "github_actions_run_bench" {
  statement {
    sid    = "RunBenchTask"
    effect = "Allow"
    actions = [
      "ecs:RunTask",
      "ecs:DescribeTasks",
      "ecs:StopTask",
      "ecs:ListTasks",
    ]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.bench.arn]
    }
  }

  statement {
    sid       = "DescribeTaskDefinition"
    effect    = "Allow"
    actions   = ["ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid       = "PassRolesToEcs"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task.arn, aws_iam_role.execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadResults"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.results.arn, "${aws_s3_bucket.results.arn}/*"]
  }

  statement {
    sid    = "ReadLogs"
    effect = "Allow"
    actions = [
      "logs:GetLogEvents",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.bench.arn}:*"]
  }
}

resource "aws_iam_role_policy" "github_actions_run_bench" {
  name   = "run-bench"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_run_bench.json
}
