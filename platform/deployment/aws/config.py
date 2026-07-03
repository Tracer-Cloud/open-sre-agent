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
INSTANCE_TYPE = "t3.micro"
AL2023_AMI_SSM_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
# Ubuntu 22.04 LTS (Jammy) — ships glibc 2.35, required by the pre-built opensre
# PyInstaller binary.  AL2023 only ships glibc 2.34 so the binary fails there.
UBUNTU2204_AMI_SSM_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
)
EC2_ROOT_DEVICE_NAME = "/dev/xvda"  # Amazon Linux 2023
EC2_UBUNTU_ROOT_DEVICE_NAME = "/dev/sda1"  # Ubuntu official AMIs (22.04+)
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

# ─── Gateway AMI baking ────────────────────────────────────────────────────────
GATEWAY_AMI_NAME_PREFIX = "opensre-gateway"
GATEWAY_BUILDER_INSTANCE_TYPE = "t3.small"
GATEWAY_AMI_WAITER_DELAY_SECONDS = 30
GATEWAY_AMI_WAITER_MAX_ATTEMPTS = 40  # 20 minutes max
GATEWAY_AMI_GIT_REF_ENV = "OPENSRE_GATEWAY_GIT_REF"
GATEWAY_AMI_ID_ENV = "OPENSRE_GATEWAY_AMI_ID"
GATEWAY_AMI_DESTROY_PURGE_ENV = "OPENSRE_GATEWAY_DESTROY_PURGE_AMI"
