"""Named resources for a Fargate environment (no AWS calls)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from platform.deployment.fargate import config as cfg


@dataclass(frozen=True)
class FargateStack:
    """Logical names for one ENV (e.g. staging)."""

    env: str
    region: str
    cluster_name: str
    web_service_name: str
    slack_gateway_service_name: str
    secrets_prefix: str

    def secret_id(self, name: str) -> str:
        return f"{self.secrets_prefix}/{name}"


def resolve_env_name(raw: str | None = None) -> str:
    value = (raw or os.getenv(cfg.FARGATE_ENV_NAME) or "staging").strip().lower()
    if value in {"prod", "production"}:
        return "production"
    if value in {"stage", "staging"}:
        return "staging"
    if not value:
        return "staging"
    return value


def get_stack(*, env: str | None = None, region: str | None = None) -> FargateStack:
    resolved = resolve_env_name(env)
    return FargateStack(
        env=resolved,
        region=(region or os.getenv("AWS_REGION") or cfg.DEFAULT_DEPLOY_REGION).strip(),
        cluster_name=f"{cfg.CLUSTER_NAME}-{resolved}",
        web_service_name=f"{cfg.WEB_SERVICE_NAME}-{resolved}",
        slack_gateway_service_name=f"{cfg.SLACK_GATEWAY_SERVICE_NAME}-{resolved}",
        secrets_prefix=f"/opensre/{resolved}",
    )


def describe_plan(stack: FargateStack) -> list[str]:
    """Human-readable intended resources (dry-run / plan output)."""
    return [
        f"region={stack.region}",
        f"ecs_cluster={stack.cluster_name}",
        f"ecs_service={stack.web_service_name} (ALB port {cfg.WEB_CONTAINER_PORT})",
        f"ecs_service={stack.slack_gateway_service_name} (Socket Mode, no inbound port)",
        f"rds={cfg.RDS_ENGINE} {cfg.RDS_INSTANCE_CLASS} (single-AZ, shared)",
        f"secret={stack.secret_id(cfg.SECRET_SLACK_BOT_TOKEN)}",
        f"secret={stack.secret_id(cfg.SECRET_SLACK_APP_TOKEN)}",
        f"secret={stack.secret_id(cfg.SECRET_DATABASE_URL)}",
        f"secret={stack.secret_id(cfg.SECRET_CLERK_JWKS_URL)}",
        f"s3_prefix={cfg.ARTIFACTS_BUCKET_PREFIX}/{{org_id}}/{{investigation_id}}/",
        "cloudwatch=log groups + basic alarms",
    ]
