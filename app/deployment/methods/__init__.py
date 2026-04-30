"""Deployment *methods* — concrete ways to publish OpenSRE (CLI-driven targets).

Each submodule corresponds to a shipping path (Railway, LangSmith/LangGraph), not to
post-deploy runtime checks (see :mod:`app.deployment.operations`).
"""

from app.deployment.methods.langsmith import (
    DEFAULT_DEPLOYMENT_NAME,
    LANGSMITH_API_KEY_ENV,
    LANGSMITH_DEPLOYMENT_NAME_ENV,
    extract_deployment_url,
    is_langgraph_cli_installed,
    persist_langsmith_env,
    resolve_deployment_name,
    resolve_langsmith_api_key,
    run_langsmith_deploy,
    validate_langsmith_api_key,
)
from app.deployment.methods.railway import (
    RAILWAY_DATABASE_HINTS,
    READINESS_PATH,
    DeployResult,
    DeployResultRequired,
    deploy_to_railway,
    get_railway_auth_status,
    is_railway_cli_installed,
    run_deploy,
)

__all__ = [
    "DEFAULT_DEPLOYMENT_NAME",
    "DeployResult",
    "DeployResultRequired",
    "LANGSMITH_API_KEY_ENV",
    "LANGSMITH_DEPLOYMENT_NAME_ENV",
    "RAILWAY_DATABASE_HINTS",
    "READINESS_PATH",
    "deploy_to_railway",
    "extract_deployment_url",
    "get_railway_auth_status",
    "is_langgraph_cli_installed",
    "is_railway_cli_installed",
    "persist_langsmith_env",
    "resolve_deployment_name",
    "resolve_langsmith_api_key",
    "run_deploy",
    "run_langsmith_deploy",
    "validate_langsmith_api_key",
]
