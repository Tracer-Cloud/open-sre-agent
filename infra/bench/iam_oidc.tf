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
    # Per-ARN - workflow can seed only these four LLM-key secrets.
    # No GetSecretValue here (the seed workflow writes values; it never
    # reads them back).
    resources = [
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
      aws_secretsmanager_secret.deepseek_api_key.arn,
      aws_secretsmanager_secret.hf_token.arn,
    ]
  }

  # ECR authorization. GetAuthorizationToken does not accept a resource ARN
  # (must be "*") - tfsec flags this; suppression in .tfsec/config.yml
  # under aws-iam-no-policy-wildcards already covers IAM patterns that
  # cannot be ARN-scoped.
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # ECR push - scoped to the bench repo only.
  statement {
    sid    = "EcrPushBenchImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
    ]
    resources = [aws_ecr_repository.bench.arn]
  }

  # RegisterTaskDefinition - required by benchmark-promote-image.yml to
  # register a new task definition revision pointing at the chosen ECR tag.
  # AWS does not support resource-level permissions for RegisterTaskDefinition,
  # so it must be "*"; tfsec aws-iam-no-policy-wildcards suppression covers
  # this pattern.
  statement {
    sid       = "RegisterTaskDefinition"
    effect    = "Allow"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  # Tag the task definition on Register. The AWS provider's default_tags
  # block in providers.tf attaches tags to every taggable resource, so
  # RegisterTaskDefinition implicitly calls ecs:TagResource. Scoped to the
  # bench family. (No UntagResource: skip_destroy = true on the resource
  # means Terraform never deregisters / untags old revisions.)
  statement {
    sid       = "TagBenchTaskDefinition"
    effect    = "Allow"
    actions   = ["ecs:TagResource"]
    resources = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name_prefix}:*"]
  }

  # Terraform state bucket - read+write on the opensre-bench/ prefix only,
  # so this role can run `terraform apply` from the promote-image workflow.
  # ListBucket is scoped to the bucket but conditioned on s3:prefix to
  # prevent enumerating other modules' state keys; object actions are
  # scoped to the prefix directly.
  statement {
    sid       = "TerraformStateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::tracer-cloud-tfstate-${data.aws_caller_identity.current.account_id}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["opensre-bench/*"]
    }
  }

  statement {
    sid    = "TerraformStateObject"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::tracer-cloud-tfstate-${data.aws_caller_identity.current.account_id}/opensre-bench/*"]
  }

  # Terraform state lock table - terraform apply takes/releases a lock on
  # every run. Scoped to the single tflock table.
  statement {
    sid    = "TerraformStateLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = ["arn:aws:dynamodb:${var.region}:${data.aws_caller_identity.current.account_id}:table/tracer-cloud-tflock"]
  }
}

resource "aws_iam_role_policy" "github_actions_run_bench" {
  name   = "run-bench"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_run_bench.json
}

# Broad read across all services this module touches — needed because
# benchmark-promote-image.yml runs `terraform apply`, and apply's refresh
# phase reads every resource currently in state to compute the diff.
# ReadOnlyAccess does NOT include kms:Decrypt, so secret values remain
# protected (GetSecretValue returns encrypted blobs that this role cannot
# decrypt).
resource "aws_iam_role_policy_attachment" "github_actions_readonly" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ---------------------------------------------------------------------------- #
# Second OIDC-assumed role: terraform-plan                                     #
#                                                                              #
# Used by the terraform-bench.yml workflow on PRs to run `terraform plan`.    #
# Separate from `github_actions` so PR plan-only runs don't carry the write   #
# permissions that the promote/run workflows need. Both roles get             #
# AWS-managed `ReadOnlyAccess` because plan and apply both need broad read    #
# across every resource type in this module (ECS, ECR, IAM, S3, CloudWatch). #
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
