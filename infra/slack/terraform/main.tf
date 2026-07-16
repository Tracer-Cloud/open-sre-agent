terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.40"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# The shared ECS cluster (created once by ../cluster). One silo service per
# team runs on it; the isolation boundary is this stack's task role + SGs.
data "aws_ecs_cluster" "shared" {
  cluster_name = var.cluster_name
}

locals {
  # Secrets are stored in SSM and injected into containers by ECS; empty
  # optional values get no parameter and no injection. for_each must iterate
  # plain key names — sensitive-derived collections are rejected.
  secret_values = {
    ANTHROPIC_API_KEY            = var.anthropic_api_key
    OPENAI_API_KEY               = var.openai_api_key
    SLACK_BOT_TOKEN              = var.slack_bot_token
    SLACK_APP_TOKEN              = var.slack_app_token
    OPENSRE_ALERT_LISTENER_TOKEN = var.alert_listener_token
    SLACK_WEBHOOK_URL            = var.slack_webhook_url
    DATABASE_URL                 = var.database_url
  }
  secret_keys = toset([for key, value in local.secret_values : key if nonsensitive(value != "")])

  # Slack tokens must never reach the public web task.
  gateway_only_secret_keys = toset(["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"])
  # Gateway needs Slack + LLM keys only (agent turns). Never DATABASE_URL / alert token.
  gateway_secret_keys = setunion(
    local.gateway_only_secret_keys,
    toset(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]),
  )

  alb_enabled    = var.certificate_arn != ""
  bucket_enabled = var.artifacts_bucket != ""

  llm_env_map = var.llm_provider != "" ? { LLM_PROVIDER = var.llm_provider } : {}

  # Persistent agent memory mounts here in every task; scratch stays on the
  # Fargate ephemeral disk.
  memories_mount_points = [
    { sourceVolume = "memories", containerPath = "/workspace/memories", readOnly = false }
  ]

  memories_tags = {
    component  = "opensre-memories"
    team       = var.team
    env        = var.env
    managed_by = "terraform"
  }

  web_env = [
    for key, value in merge(
      local.llm_env_map,
      local.bucket_enabled ? { OPENSRE_ARTIFACTS_BUCKET = var.artifacts_bucket } : {},
      {
        MODE                         = "web"
        PORT                         = tostring(var.web_port)
        OPENSRE_INVESTIGATION_WORKER = "1"
      },
    ) : { name = key, value = value }
  ]

  gateway_env = [
    for key, value in merge(
      local.llm_env_map,
      {
        MODE                         = "gateway"
        SLACK_GATEWAY_HEARTBEAT_PATH = var.gateway_heartbeat_path
      },
      var.slack_allowed_users != "" ? { SLACK_ALLOWED_USERS = var.slack_allowed_users } : {},
      var.slack_allow_open_workspace ? { SLACK_ALLOW_OPEN_WORKSPACE = "1" } : {},
    ) : { name = key, value = value }
  ]
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secret_keys

  name  = "/${var.name_prefix}/${lower(each.key)}"
  type  = "SecureString"
  value = local.secret_values[each.key]
}

# --- IAM ------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_base" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [for parameter in aws_ssm_parameter.secret : parameter.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${var.name_prefix}-read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_bedrock" {
  role       = aws_iam_role.task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

# Task role mount + read access to the S3 Files memory filesystem (mandatory:
# S3 Files volumes require a task IAM role). Managed policy covers the NFS
# client mount/read/write; the inline policy grants direct S3 object reads that
# S3 Files uses to optimize read performance.
resource "aws_iam_role_policy_attachment" "task_s3files_client" {
  role       = aws_iam_role.task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FilesClientFullAccess"
}

data "aws_iam_policy_document" "task_memories_read" {
  statement {
    sid       = "S3ObjectReadAccess"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.memories.arn}/*"]
  }
  statement {
    sid       = "S3BucketListAccess"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.memories.arn]
  }
}

resource "aws_iam_role_policy" "task_memories_read" {
  name   = "${var.name_prefix}-memories-read"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_memories_read.json
}

# --- Agent memory: S3 Files (persistent NFS mount) --------------------------

# Per-env-team bucket name. Env is part of the name so a team's dev and prod
# memory never collide on one global S3 name. Versioning is MANDATORY: S3 Files
# refuses to attach without it, and it is how the filesystem syncs to S3.
resource "aws_s3_bucket" "memories" {
  bucket = "opensre-memories-${var.env}-${var.team}"
  tags   = local.memories_tags
}

resource "aws_s3_bucket_versioning" "memories" {
  bucket = aws_s3_bucket.memories.id
  versioning_configuration {
    status = "Enabled"
  }
}

# S3 Files requires SSE-S3 or SSE-KMS on the bucket.
resource "aws_s3_bucket_server_side_encryption_configuration" "memories" {
  bucket = aws_s3_bucket.memories.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "memories" {
  bucket                  = aws_s3_bucket.memories.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Append-heavy JSONL memory churns object versions; expire noncurrent versions
# so storage does not grow unbounded.
resource "aws_s3_bucket_lifecycle_configuration" "memories" {
  bucket = aws_s3_bucket.memories.id
  rule {
    id     = "expire-noncurrent-memory-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.memories_noncurrent_version_expiration_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.memories]
}

# Role S3 Files assumes to sync the filesystem with the bucket. Trust principal
# is elasticfilesystem.amazonaws.com (S3 Files runs on EFS infrastructure),
# scoped to S3 Files file-system source ARNs in this account.
data "aws_iam_policy_document" "s3files_service_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticfilesystem.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:s3files:${var.region}:${data.aws_caller_identity.current.account_id}:file-system/*"]
    }
  }
}

resource "aws_iam_role" "s3files_service" {
  name               = "${var.name_prefix}-s3files-service"
  assume_role_policy = data.aws_iam_policy_document.s3files_service_assume.json
}

data "aws_iam_policy_document" "s3files_service" {
  statement {
    sid       = "S3BucketPermissions"
    actions   = ["s3:ListBucket", "s3:ListBucketVersions"]
    resources = [aws_s3_bucket.memories.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
  statement {
    sid       = "S3ObjectPermissions"
    actions   = ["s3:AbortMultipartUpload", "s3:DeleteObject*", "s3:GetObject*", "s3:List*", "s3:PutObject*"]
    resources = ["${aws_s3_bucket.memories.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
  # S3 Files manages EventBridge rules (prefixed DO-NOT-DELETE-S3-Files) to
  # detect out-of-band bucket changes and trigger sync.
  statement {
    sid       = "EventBridgeManage"
    actions   = ["events:DeleteRule", "events:DisableRule", "events:EnableRule", "events:PutRule", "events:PutTargets", "events:RemoveTargets"]
    resources = ["arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"]
    condition {
      test     = "StringEquals"
      variable = "events:ManagedBy"
      values   = ["elasticfilesystem.amazonaws.com"]
    }
  }
  statement {
    sid       = "EventBridgeRead"
    actions   = ["events:DescribeRule", "events:ListRuleNamesByTarget", "events:ListRules", "events:ListTargetsByRule"]
    resources = ["arn:aws:events:*:*:rule/*"]
  }
}

resource "aws_iam_role_policy" "s3files_service" {
  name   = "${var.name_prefix}-s3files-service"
  role   = aws_iam_role.s3files_service.id
  policy = data.aws_iam_policy_document.s3files_service.json
}

resource "aws_s3files_file_system" "memories" {
  bucket                = aws_s3_bucket.memories.arn
  role_arn              = aws_iam_role.s3files_service.arn
  accept_bucket_warning = true
  tags                  = local.memories_tags

  depends_on = [
    aws_s3_bucket_versioning.memories,
    aws_s3_bucket_server_side_encryption_configuration.memories,
    aws_iam_role_policy.s3files_service,
  ]
}

# NFS mount target per subnet, reachable only from this team's task SGs.
resource "aws_security_group" "memories_mount" {
  name        = "${var.name_prefix}-memories-mount"
  description = "S3 Files mount target — NFS 2049 from this team's tasks only"
  vpc_id      = data.aws_vpc.default.id
  tags        = local.memories_tags
}

# Cross-team isolation on shared subnets: NFS is reachable only from this
# team's own task SGs, never from any other task in the VPC. The task SGs
# already allow all egress, so the outbound 2049 side needs no extra rule.
resource "aws_vpc_security_group_ingress_rule" "memories_from_gateway" {
  security_group_id            = aws_security_group.memories_mount.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.gateway.id
}

resource "aws_vpc_security_group_ingress_rule" "memories_from_web" {
  security_group_id            = aws_security_group.memories_mount.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.web.id
}

resource "aws_s3files_mount_target" "memories" {
  for_each = toset(data.aws_subnets.default.ids)

  file_system_id  = aws_s3files_file_system.memories.id
  subnet_id       = each.value
  security_groups = [aws_security_group.memories_mount.id]
}

# One access point per team pins the POSIX identity and chroots the mount to
# the team's subtree (the IAM + POSIX dual permission model).
resource "aws_s3files_access_point" "memories" {
  file_system_id = aws_s3files_file_system.memories.id

  posix_user {
    uid = var.memories_uid
    gid = var.memories_gid
  }

  root_directory {
    path = "/memories"
    creation_permissions {
      owner_uid   = var.memories_uid
      owner_gid   = var.memories_gid
      permissions = "0755"
    }
  }

  tags = local.memories_tags
}

# --- Artifacts bucket (optional, enabled by artifacts_bucket) ---------------

resource "aws_s3_bucket" "artifacts" {
  count = local.bucket_enabled ? 1 : 0

  bucket = var.artifacts_bucket
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count = local.bucket_enabled ? 1 : 0

  bucket                  = aws_s3_bucket.artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "write_artifacts" {
  count = local.bucket_enabled ? 1 : 0

  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts[0].arn}/*"]
  }
}

resource "aws_iam_role_policy" "task_artifacts" {
  count = local.bucket_enabled ? 1 : 0

  name   = "${var.name_prefix}-write-artifacts"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.write_artifacts[0].json
}

# --- Networking -----------------------------------------------------------

resource "aws_security_group" "web" {
  name        = "${var.name_prefix}-web"
  description = "OpenSRE web API"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Without an ALB the web port is reachable directly; with one, only from the ALB.
resource "aws_vpc_security_group_ingress_rule" "web_direct" {
  count = local.alb_enabled ? 0 : 1

  security_group_id = aws_security_group.web.id
  from_port         = var.web_port
  to_port           = var.web_port
  ip_protocol       = "tcp"
  cidr_ipv4         = var.web_ingress_cidr
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  count = local.alb_enabled ? 1 : 0

  security_group_id            = aws_security_group.web.id
  from_port                    = var.web_port
  to_port                      = var.web_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb[0].id
}

resource "aws_security_group" "alb" {
  count = local.alb_enabled ? 1 : 0

  name        = "${var.name_prefix}-alb"
  description = "OpenSRE web API load balancer"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.web_ingress_cidr]
  }

  ingress {
    description = "HTTP (redirected to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.web_ingress_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "gateway" {
  name        = "${var.name_prefix}-gateway"
  description = "OpenSRE Slack gateway (egress only)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- ALB (optional, enabled by certificate_arn) ----------------------------

resource "aws_lb" "web" {
  count = local.alb_enabled ? 1 : 0

  name               = "${var.name_prefix}-web"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[0].id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "web" {
  count = local.alb_enabled ? 1 : 0

  name        = "${var.name_prefix}-web"
  port        = var.web_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  health_check {
    # /healthz is pure liveness; /health returns 503 until an LLM is configured.
    path    = "/healthz"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  count = local.alb_enabled ? 1 : 0

  load_balancer_arn = aws_lb.web[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web[0].arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  count = local.alb_enabled ? 1 : 0

  load_balancer_arn = aws_lb.web[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# --- ECS ------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "web" {
  name              = "/${var.name_prefix}/web"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/${var.name_prefix}/gateway"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name_prefix}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "memories"
    s3files_volume_configuration {
      file_system_arn  = aws_s3files_file_system.memories.arn
      access_point_arn = aws_s3files_access_point.memories.arn
    }
  }

  container_definitions = jsonencode([
    {
      name      = "opensre-web"
      image     = var.image_uri
      essential = true
      portMappings = [
        { containerPort = var.web_port, protocol = "tcp" }
      ]
      environment = local.web_env
      mountPoints = local.memories_mount_points
      secrets = [
        for key, parameter in aws_ssm_parameter.secret :
        { name = key, valueFrom = parameter.arn }
        if !contains(local.gateway_only_secret_keys, key)
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "web"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${var.name_prefix}-gateway"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  volume {
    name = "memories"
    s3files_volume_configuration {
      file_system_arn  = aws_s3files_file_system.memories.arn
      access_point_arn = aws_s3files_access_point.memories.arn
    }
  }

  container_definitions = jsonencode([
    {
      name        = "opensre-gateway"
      image       = var.image_uri
      essential   = true
      environment = local.gateway_env
      mountPoints = local.memories_mount_points
      # The gateway serves no HTTP port (Socket Mode is an outbound websocket).
      # The worker refreshes this heartbeat file while the connection is live;
      # a dropped connection or a wedged worker lets it go stale, so a stale
      # file (> threshold) marks the task unhealthy and ECS restarts it.
      healthCheck = {
        command = ["CMD-SHELL",
          "test -f ${var.gateway_heartbeat_path} && [ $(( $(date +%s) - $(stat -c %Y ${var.gateway_heartbeat_path}) )) -lt ${var.gateway_heartbeat_stale_seconds} ]"
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      # Least privilege: Slack + LLM only — never DATABASE_URL / alert token.
      secrets = [
        for key, parameter in aws_ssm_parameter.secret :
        { name = key, valueFrom = parameter.arn }
        if contains(local.gateway_secret_keys, key)
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.gateway.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "gateway"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "web" {
  name            = "${var.name_prefix}-web"
  cluster         = data.aws_ecs_cluster.shared.arn
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = local.alb_enabled ? 60 : null

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true
  }

  dynamic "load_balancer" {
    for_each = local.alb_enabled ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.web[0].arn
      container_name   = "opensre-web"
      container_port   = var.web_port
    }
  }
}

resource "aws_ecs_service" "gateway" {
  name            = "${var.name_prefix}-gateway"
  cluster         = data.aws_ecs_cluster.shared.arn
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Stop-then-start: Socket Mode is single-consumer and the per-conversation
  # turn lock is in-process, so exactly one gateway task per team may run.
  # ECS defaults (100/200) would run old+new tasks concurrently on deploy and
  # Slack would round-robin events between them (split-consumer replies).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.gateway.id]
    assign_public_ip = true
  }
}

check "slack_access_control" {
  assert {
    condition     = var.slack_allowed_users != "" || var.slack_allow_open_workspace
    error_message = "Set slack_allowed_users (recommended) or slack_allow_open_workspace=true (dogfood only). Never commit secrets — use TF_VAR_* / gitignored tfvars."
  }
}
