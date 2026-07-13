"""Constants for the OpenSRE Fargate staging/prod layout."""

from __future__ import annotations

from platform.deployment.aws.config import DEFAULT_REGION, WEB_API_PORT

# Cluster / services (suffix with ENV via stack helpers)
CLUSTER_NAME = "opensre-cluster"
WEB_SERVICE_NAME = "opensre-web"
SLACK_GATEWAY_SERVICE_NAME = "opensre-slack-gateway"

WEB_CONTAINER_PORT = WEB_API_PORT
RDS_INSTANCE_CLASS = "db.t3.medium"
RDS_ENGINE = "postgres"

# Secrets Manager paths — /opensre/{env}/...
SECRET_SLACK_BOT_TOKEN = "slack_bot_token"
SECRET_SLACK_APP_TOKEN = "slack_app_token"
SECRET_DATABASE_URL = "database_url"
SECRET_CLERK_JWKS_URL = "clerk_jwks_url"

# Refuse live apply unless set to 1/true/yes
FARGATE_CONFIRM_ENV = "OPENSRE_FARGATE_CONFIRM"
FARGATE_ENV_NAME = "ENV"  # staging | production (Makefile: ENV=staging)

DEFAULT_DEPLOY_REGION = DEFAULT_REGION
ARTIFACTS_BUCKET_PREFIX = "opensre-investigation-artifacts"
