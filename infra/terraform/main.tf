terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
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
      { MODE = "gateway" },
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

resource "aws_ecs_cluster" "this" {
  name = var.name_prefix
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name_prefix}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "opensre-web"
      image     = var.image_uri
      essential = true
      portMappings = [
        { containerPort = var.web_port, protocol = "tcp" }
      ]
      environment = local.web_env
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

  container_definitions = jsonencode([
    {
      name        = "opensre-gateway"
      image       = var.image_uri
      essential   = true
      environment = local.gateway_env
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
  cluster         = aws_ecs_cluster.this.id
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
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = 1
  launch_type     = "FARGATE"

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
