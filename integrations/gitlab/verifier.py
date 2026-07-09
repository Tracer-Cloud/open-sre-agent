"""GitLab integration verifier."""

from __future__ import annotations

from typing import Any

from integrations.gitlab import build_gitlab_config, validate_gitlab_config
from integrations.verification import register_verifier, result


@register_verifier("gitlab")
def verify_gitlab(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Validate GitLab credentials through the shared GitLab config probe."""
    try:
        normalized_config = build_gitlab_config(config)
    except Exception as exc:
        return result("gitlab", source, "missing", str(exc))

    validation_result = validate_gitlab_config(normalized_config)
    if not normalized_config.auth_token:
        return result("gitlab", source, "missing", validation_result.detail)

    return result(
        "gitlab",
        source,
        "passed" if validation_result.ok else "failed",
        validation_result.detail,
    )
