"""AWS deployment configuration constants."""

from __future__ import annotations

# ─── Region ───────────────────────────────────────────────────────────────────
DEFAULT_REGION = "us-east-1"

# ─── Boto3 client ─────────────────────────────────────────────────────────────
BOTO3_RETRY_MAX_ATTEMPTS = 3
BOTO3_CONNECT_TIMEOUT_SECONDS = 10
BOTO3_READ_TIMEOUT_SECONDS = 30

# ─── Resource tags ────────────────────────────────────────────────────────────
STACK_TAG_KEY = "tracer:stack"
MANAGED_TAG_KEY = "tracer:managed"
MANAGED_TAG_VALUE = "sdk"

# ─── EC2 instance ─────────────────────────────────────────────────────────────
INSTANCE_TYPE = "t2.micro"
AL2023_AMI_SSM_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
EC2_ROOT_DEVICE_NAME = "/dev/xvda"
EC2_VOLUME_SIZE_GB = 30
EC2_VOLUME_TYPE = "gp3"
EC2_INSTANCE_ROLE_DESCRIPTION = "EC2 instance role for OpenSRE deployment"
EC2_WAITER_DELAY_SECONDS = 10
EC2_WAITER_MAX_ATTEMPTS = 30

# ─── IAM managed policy ARNs ──────────────────────────────────────────────────
ECR_READ_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
BEDROCK_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
SSM_MANAGED_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

# ─── IAM propagation ──────────────────────────────────────────────────────────
IAM_PROFILE_PROPAGATION_SECONDS = 10

# ─── Security groups ──────────────────────────────────────────────────────────
SG_DELETE_MAX_ATTEMPTS = 12
SG_DELETE_RETRY_DELAY_SECONDS = 10
DEFAULT_INGRESS_CIDR = "0.0.0.0/0"

# ─── SSM ──────────────────────────────────────────────────────────────────────
SSM_REGISTRATION_POLL_INTERVAL_SECONDS = 10
SSM_REGISTRATION_MAX_ATTEMPTS = 30
SSM_CMD_POLL_INTERVAL_SECONDS = 5
SSM_CMD_POLL_ATTEMPTS = 24
SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS = 10
SSM_PROVISION_CMD_POLL_ATTEMPTS = 60
SSM_SHELL_DOCUMENT = "AWS-RunShellScript"
SSM_TERMINAL_STATUSES = ("Success", "Failed", "Cancelled", "TimedOut", "Undeliverable")

# ─── ECR / Docker ─────────────────────────────────────────────────────────────
ECR_DEFAULT_IMAGE_TAG = "latest"
ECR_DOCKER_PLATFORM = "linux/amd64"
ECR_SCAN_ON_PUSH = True
ECR_IMAGE_TAG_MUTABILITY = "MUTABLE"

# ─── EC2 instance provisioning (via SSM) ──────────────────────────────────────
PROVISION_ECR_AUTH_MAX_ATTEMPTS = 5
PROVISION_ECR_AUTH_RETRY_SECONDS = 10
DOCKER_BIN = "/usr/bin/docker"

# ─── Gateway health checks (via SSM) ──────────────────────────────────────────
GATEWAY_HEALTH_POLL_INTERVAL_SECONDS = 15
GATEWAY_HEALTH_MAX_ATTEMPTS = 60
GATEWAY_LOG_TAIL_LINES = 200
GATEWAY_READY_LOG_SENTINEL = "polling started"
