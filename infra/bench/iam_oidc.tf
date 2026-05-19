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
  description        = "Assumed by GitHub Actions in ${var.github_repository} to launch bench tasks AND seed secret values."
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

  statement {
    sid    = "SeedSecretValues"
    effect = "Allow"
    actions = [
      "secretsmanager:PutSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    # Per-ARN — workflow can seed only these four LLM-key secrets.
    # No GetSecretValue here (the seed workflow writes values; it never
    # reads them back).
    resources = [
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
      aws_secretsmanager_secret.deepseek_api_key.arn,
      aws_secretsmanager_secret.hf_token.arn,
    ]
  }
}

resource "aws_iam_role_policy" "github_actions_run_bench" {
  name   = "run-bench"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_run_bench.json
}

# ---------------------------------------------------------------------------- #
# Second OIDC-assumed role: terraform-plan                                     #
#                                                                              #
# Used by the terraform-bench.yml workflow on PRs to run `terraform plan`.    #
# Separate from `github_actions` because plan needs broad read across every   #
# resource type in this module (ECS, ECR, IAM, S3, CloudWatch, etc.) —        #
# AWS-managed `ReadOnlyAccess` covers it. The `github_actions` role is        #
# narrower (RunTask + Seed) and shouldn't pick up broad read.                  #
# ---------------------------------------------------------------------------- #

resource "aws_iam_role" "terraform_plan" {
  name               = "${local.name_prefix}-terraform-plan"
  description        = "Assumed by GitHub Actions on PRs to run terraform plan against ${local.name_prefix} state."
  assume_role_policy = data.aws_iam_policy_document.github_actions_trust.json
}

# Broad read across all services this module touches — needed for plan diff.
resource "aws_iam_role_policy_attachment" "terraform_plan_readonly" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Explicit state-bucket read (ListBucket gives clean 404 instead of 403 on
# missing state file; GetObject reads the state itself).
data "aws_iam_policy_document" "terraform_plan_state_read" {
  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::tracer-cloud-tfstate-${data.aws_caller_identity.current.account_id}"]
  }

  statement {
    sid       = "ReadStateObject"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::tracer-cloud-tfstate-${data.aws_caller_identity.current.account_id}/opensre-bench/*"]
  }
}

resource "aws_iam_role_policy" "terraform_plan_state_read" {
  name   = "state-read"
  role   = aws_iam_role.terraform_plan.id
  policy = data.aws_iam_policy_document.terraform_plan_state_read.json
}

# Bring back the data source dropped earlier — needed for the state bucket
# ARN computed from account id.
data "aws_caller_identity" "current" {}
