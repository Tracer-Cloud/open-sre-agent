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
        if _is_provider_connectivity_error(msg):
            raise OpenSREError(
                str(exc),
                suggestion=_provider_connectivity_suggestion(msg),
            ) from exc
        if "anthropic" in msg and "model" in msg and "was not found" in msg:
            raise OpenSREError(
                str(exc),
                suggestion=(
                    "Verify your model name in ANTHROPIC_REASONING_MODEL or "
                    "ANTHROPIC_TOOLCALL_MODEL environment variables."
                ),
            ) from exc

    raise exc


def _is_provider_connectivity_error(message: str) -> bool:
    """Return true for LLM provider reachability failures that need CLI UX."""
    return (
        ("cannot connect to" in message and " api" in message)
        or "api request timed out" in message
        or "check that the service is running and responsive" in message
    )


def _provider_connectivity_suggestion(message: str) -> str:
    if "ollama" in message:
        return "Start Ollama with `ollama serve`, or rerun `opensre onboard local_llm`."
    return (
        "Check that your LLM provider endpoint is reachable, then rerun "
        "`opensre onboard` if configuration changed."
    )
