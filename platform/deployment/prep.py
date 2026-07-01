"""Pre-deploy checks: environment validation and existing-stack cleanup."""

from __future__ import annotations

import os
import sys

import boto3

from config.config import get_configured_llm_provider, get_llm_provider_api_key_env
from config.llm_auth import KEYLESS_PROVIDER_VALUES, SUPPORTED_PROVIDER_VALUES, provider_spec
from config.local_env import bootstrap_opensre_env, get_project_env_path
from platform.deployment.aws.client import DEFAULT_REGION
from platform.deployment.aws.ec2 import find_stack_instance_ids, terminate_instance
from platform.deployment.stack import get_stack, outputs_exists

_DEPLOY_ENV_EXAMPLE = ".env.deploy.example"
_ABORT_IF_EXISTS_ENV = "OPENSRE_DEPLOY_ABORT_IF_EXISTS"


def _env_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _aws_credentials_available() -> bool:
    if _env_set("AWS_ROLE_ARN"):
        return True
    if _env_set("AWS_ACCESS_KEY_ID") and _env_set("AWS_SECRET_ACCESS_KEY"):
        return True
    if _env_set("AWS_PROFILE"):
        return True
    try:
        credentials = boto3.Session().get_credentials()
    except Exception:  # noqa: BLE001
        return False
    return credentials is not None


def _collect_deploy_env_issues() -> tuple[list[str], list[str]]:
    """Return ``(missing_required, warnings)`` for the current process env."""
    bootstrap_opensre_env(override=False)

    missing: list[str] = []
    warnings: list[str] = []

    if not _aws_credentials_available():
        missing.append(
            "AWS credentials — set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, "
            "AWS_ROLE_ARN, AWS_PROFILE, or ~/.aws/credentials"
        )

    if not _env_set("TELEGRAM_BOT_TOKEN"):
        missing.append("TELEGRAM_BOT_TOKEN")

    if not _env_set("TELEGRAM_ALLOWED_USERS"):
        warnings.append("TELEGRAM_ALLOWED_USERS (recommended gateway pairing gate)")

    provider = get_configured_llm_provider()
    if provider not in SUPPORTED_PROVIDER_VALUES:
        missing.append(
            f"LLM_PROVIDER (unsupported value '{provider}'; "
            f"expected one of: {', '.join(SUPPORTED_PROVIDER_VALUES)})"
        )
    else:
        api_key_env = get_llm_provider_api_key_env(provider)
        if api_key_env and not _env_set(api_key_env):
            missing.append(f"{api_key_env} (required for LLM_PROVIDER={provider})")
        elif provider in KEYLESS_PROVIDER_VALUES:
            spec = provider_spec(provider)
            if spec is not None and spec.credential_kind in {"cli", "local"}:
                warnings.append(
                    f"LLM_PROVIDER={provider} uses {spec.credential_kind} auth and "
                    "is unlikely to work inside EC2 containers"
                )

    return missing, warnings


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR", "").strip() == ""


def _highlight(text: str, *, kind: str) -> str:
    if not _supports_color():
        return text
    if kind == "missing":
        return f"\033[31m{text}\033[0m"
    if kind == "warn":
        return f"\033[33m{text}\033[0m"
    if kind == "label":
        return f"\033[1m{text}\033[0m"
    return text


def _print_deploy_env_report(missing: list[str], warnings: list[str]) -> None:
    env_path = get_project_env_path()
    print("=" * 60)
    print(_highlight("Deploy environment validation", kind="label"))
    print("=" * 60)

    if missing:
        print()
        print(_highlight("Missing required:", kind="label"))
        for item in missing:
            print(f"  {_highlight('MISSING', kind='missing')}: {item}")

    if warnings:
        print()
        print(_highlight("Recommended:", kind="label"))
        for item in warnings:
            print(f"  {_highlight('WARN', kind='warn')}: {item}")

    if missing or warnings:
        print()
        print(f"Env file: {env_path}")
        print(f"Template: {_DEPLOY_ENV_EXAMPLE}")
        print()


def validate_deploy_env() -> None:
    """Fail fast when required deploy environment variables are missing."""
    missing, warnings = _collect_deploy_env_issues()
    if not missing and not warnings:
        return

    _print_deploy_env_report(missing, warnings)

    if missing:
        raise RuntimeError(
            f"Deploy aborted: {len(missing)} required environment variable(s) missing. "
            f"Fix the items above and retry."
        )


def _abort_if_exists_enabled() -> bool:
    return os.getenv(_ABORT_IF_EXISTS_ENV, "").strip().lower() in {"1", "true", "yes"}


def cleanup_existing_deployment(*, region: str = DEFAULT_REGION) -> bool:
    """Destroy a prior deployment when outputs or stack-tagged instances exist.

    Terminates all active stack instances first so orphaned instances from a
    prior redeploy do not block security-group cleanup.

    Returns True when cleanup ran.
    """
    stack = get_stack()
    has_outputs = outputs_exists()
    instance_ids = find_stack_instance_ids(stack.stack_name, region=region)

    if not has_outputs and not instance_ids:
        return False

    if _abort_if_exists_enabled():
        raise RuntimeError(
            "Existing deployment detected "
            f"(outputs file and/or {len(instance_ids)} active instance(s)). "
            "Run `make destroy` first, or unset OPENSRE_DEPLOY_ABORT_IF_EXISTS."
        )

    print("=" * 60)
    print("Existing deployment detected — destroying previous stack")
    if instance_ids:
        print(f"  Active instances: {', '.join(instance_ids)}")
    if has_outputs:
        print("  Outputs file: present")
    print("=" * 60)
    print()

    for instance_id in instance_ids:
        print(f"Terminating stack instance {instance_id}...")
        terminate_instance(instance_id, region)

    if has_outputs:
        from platform.deployment.lifecycle import destroy

        destroy()
    elif instance_ids:
        print("No outputs file — skipped security group and IAM cleanup.")

    print()
    return True
