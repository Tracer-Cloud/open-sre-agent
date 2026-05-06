"""Map low-level CLI runtime errors to user-facing CLI errors."""

from __future__ import annotations

from typing import NoReturn


def reraise_cli_runtime_error(exc: BaseException) -> NoReturn:
    """Convert CLI auth/setup failures to structured CLI UX errors."""
    from app.cli.support.errors import OpenSREError
    from app.integrations.llm_cli.errors import CLIAuthenticationRequired

    if isinstance(exc, CLIAuthenticationRequired):
        raise OpenSREError(
            f"{exc.provider} CLI is not authenticated.",
            suggestion=f"{exc.auth_hint} ({exc.detail})",
        ) from exc

    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "cli not found" in msg or "not found on path" in msg:
            raise OpenSREError(
                "CLI tool is not installed or not found.",
                suggestion=str(exc),
            ) from exc

    raise exc
