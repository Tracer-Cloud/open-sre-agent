"""Map low-level CLI runtime errors to user-facing CLI errors."""

from __future__ import annotations

from typing import NoReturn

_INTEGRATION_FAILURE_HINTS: tuple[tuple[str, str, str], ...] = (
    ("datadog", "Datadog", "datadog"),
    ("dd_api_key", "Datadog", "datadog"),
    ("grafana", "Grafana", "grafana"),
    ("grafana_read_token", "Grafana", "grafana"),
    ("alertmanager", "Alertmanager", "alertmanager"),
    ("vercel", "Vercel", "vercel"),
)


def _integration_failure_hint(message: str) -> tuple[str, str] | None:
    lowered = message.lower()
    for needle, name, slug in _INTEGRATION_FAILURE_HINTS:
        if needle in lowered:
            return name, slug
    if any(token in lowered for token in ("401", "403", "unauthorized", "not configured")):
        if "datadog" in lowered or "dd_" in lowered:
            return "Datadog", "datadog"
        if "grafana" in lowered:
            return "Grafana", "grafana"
        if "alertmanager" in lowered:
            return "Alertmanager", "alertmanager"
    for env_var, name, slug in (
        ("dd_api_key", "Datadog", "datadog"),
        ("grafana_read_token", "Grafana", "grafana"),
        ("alertmanager_url", "Alertmanager", "alertmanager"),
    ):
        if env_var in lowered and any(
            token in lowered for token in ("missing", "not set", "unavailable", "required")
        ):
            return name, slug
    return None


def reraise_cli_runtime_error(exc: BaseException) -> NoReturn:
    """Convert CLI auth/setup failures to structured CLI UX errors."""
    from core.llm_invoke_errors import classify_llm_invoke_failure
    from integrations.llm_cli.errors import CLIAuthenticationRequired
    from interactive_shell.utils.error_handling.errors import OpenSREError

    if isinstance(exc, CLIAuthenticationRequired):
        raise OpenSREError(
            f"{exc.provider} CLI is not authenticated.",
            suggestion=f"{exc.auth_hint} ({exc.detail})",
        ) from exc

    classified = classify_llm_invoke_failure(exc)
    if classified is not None:
        suggestion = (
            "\n".join(classified.remediation_steps) if classified.remediation_steps else None
        )
        raise OpenSREError(classified.user_message, suggestion=suggestion) from exc

    integration_hint = _integration_failure_hint(str(exc))
    if integration_hint is not None:
        name, slug = integration_hint
        raise OpenSREError(
            str(exc),
            suggestion=(
                f"Verify {name} credentials with `opensre integrations verify {slug}` "
                "or re-run `opensre onboard` for that integration."
            ),
        ) from exc

    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "cli not found" in msg or "not found on path" in msg:
            raise OpenSREError(
                "CLI tool is not installed or not found.",
                suggestion=str(exc),
            ) from exc
        if (
            "prompt too long" in msg
            and "auth status could not be verified before invocation" in msg
        ):
            raise OpenSREError(
                "LLM invocation failed.",
                suggestion=str(exc),
            ) from exc
        if "anthropic" in msg and "model" in msg and "was not found" in msg:
            raise OpenSREError(
                str(exc),
                suggestion="Verify your model name in ANTHROPIC_REASONING_MODEL or ANTHROPIC_TOOLCALL_MODEL environment variables.",
            ) from exc
        if "bedrock model" in msg and "not available for your account" in msg:
            raise OpenSREError(
                str(exc),
                suggestion=(
                    "Enable access to the configured Bedrock model in the AWS region, "
                    "verify the AWS Marketplace subscription/payment setup, and ensure "
                    "the IAM user or role can use aws-marketplace:ViewSubscriptions "
                    "and aws-marketplace:Subscribe."
                ),
            ) from exc

    raise exc
