"""Classify LLM invoke failures for investigation and CLI error mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMInvokeFailure:
    """User-facing investigation failure derived from an LLM invoke exception."""

    user_message: str
    tracker_message: str
    remediation_steps: list[str]
    root_cause_category: str = "Configuration Error"


def _timeout_remediation() -> list[str]:
    return [
        (
            "CLI providers: raise the per-provider timeout env "
            + "(e.g. GEMINI_CLI_TIMEOUT_SECONDS, CLAUDE_CODE_TIMEOUT_SECONDS, "
            + "ANTIGRAVITY_CLI_TIMEOUT_SECONDS; clamped 30–600 where supported)."
        ),
        (
            "API providers (Anthropic, OpenAI, etc.): each ReAct turn is limited to "
            + "~90s per HTTP request; retry or switch to a faster model if turns time out."
        ),
        "Investigation runs many LLM and tool steps — total wall time can be several minutes.",
    ]


def _looks_like_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        from anthropic import APITimeoutError as AnthropicTimeoutError
    except ImportError:
        pass
    else:
        if isinstance(exc, AnthropicTimeoutError):
            return True

    try:
        from openai import APITimeoutError as OpenAITimeoutError
    except ImportError:
        pass
    else:
        if isinstance(exc, OpenAITimeoutError):
            return True

    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return True
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, TimeoutError):
            return True
        next_cause = cause.__cause__ or cause.__context__
        if next_cause is cause:
            break
        cause = next_cause if isinstance(next_cause, BaseException) else None
    return False


def _is_llm_cli_error(exc: BaseException, class_name: str) -> bool:
    """True when *exc* is ``integrations.llm_cli.errors.<class_name>`` (matched by name)."""
    exc_type = type(exc)
    return exc_type.__name__ == class_name and exc_type.__module__.endswith(
        "integrations.llm_cli.errors"
    )


def is_cli_timeout_error(exc: BaseException) -> bool:
    """Return True when *exc* is a CLI subprocess timeout (expected on slow turns)."""
    return _is_llm_cli_error(exc, "CLITimeoutError")


# Turn-error kinds staged when the conversational/action LLM was the intended
# route for the user's input but the provider failed before a normal reply.
# The prompt-log recorder uses this set to report a failed LLM turn (model
# "unknown" plus ``ai_error_kind``) instead of a terminal-action turn
# (``no_conversational_agent``). Terminal-path kinds (investigation failure
# categories, background-task "timeout"/"cli_exit_nonzero", slash outcomes)
# must never appear here.
LLM_PROVIDER_FAILURE_KINDS = frozenset(
    {
        "llm_unavailable",  # reasoning client import/creation failed
        "llm_timeout",  # conversational stream timed out
        "assistant_error",  # conversational stream failed mid-turn
        "action_agent_error",  # action-selection LLM failed for conversational input
    }
)

_NOT_CONFIGURED_PATTERNS = (
    "_api_key",  # env-var style, e.g. "requires ANTHROPIC_API_KEY to be set"
    "api key is not set",
    "missing api key",
    "not available for your account",
    "marketplace",
    "inference profile",
    "not configured",
    "no llm provider",
    "llm client unavailable",
    "billing is not enabled",
)
_QUOTA_PATTERNS = ("429", "quota", "rate limit", "too many requests", "credit")
_AUTH_PATTERNS = (
    "authentication",
    "unauthorized",
    "401",
    "403",
    "forbidden",
    "invalid api key",
    "incorrect api key",
    "invalid api_key",
    "incorrect api_key",
    "api_key is invalid",
    "x-api-key",
)


def classify_provider_error_kind(message: str) -> str:
    """Bucket an LLM provider failure message for analytics filtering.

    Returns one of ``not_configured``, ``quota``, ``auth``, or
    ``provider_error`` so downstream dashboards can filter provider failures
    without regexing over response text.
    """
    text = message.lower()
    if any(pattern in text for pattern in _AUTH_PATTERNS):
        return "auth"
    if any(pattern in text for pattern in _QUOTA_PATTERNS):
        return "quota"
    if any(pattern in text for pattern in _NOT_CONFIGURED_PATTERNS) or (
        "model" in text and "not found" in text
    ):
        return "not_configured"
    return "provider_error"


def classify_llm_invoke_failure(exc: BaseException) -> LLMInvokeFailure | None:
    """Return a structured failure when *exc* is a known operational LLM error.

    Returns ``None`` to signal the caller should re-raise. In particular,
    :class:`LLMCreditExhaustedError` is intentionally NOT classified — it
    represents a non-recoverable billing condition that callers must halt
    on, not wrap into a degraded result.
    """
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    # Fatal — propagate to the runner / operator. Do NOT wrap into the
    # generic "rate-limited" classification (which the text branch below
    # would otherwise match against "credit balance too low" / "quota").
    if isinstance(exc, LLMCreditExhaustedError):
        return None

    if _is_llm_cli_error(exc, "CLIAuthenticationRequired"):
        provider = getattr(exc, "provider", None) or "unknown"
        return LLMInvokeFailure(
            user_message=(
                f"The {provider} CLI is not authenticated, so the "
                "investigation could not call the model."
            ),
            tracker_message="Failed: CLI not authenticated",
            remediation_steps=[
                step
                for step in (
                    getattr(exc, "auth_hint", None),
                    getattr(exc, "detail", None),
                    "Run `opensre doctor` to verify CLI installation and auth.",
                )
                if step
            ],
        )

    if is_cli_timeout_error(exc):
        detail = str(exc).strip() or "The CLI subprocess exceeded its time limit."
        return LLMInvokeFailure(
            user_message=f"Investigation stopped: {detail}",
            tracker_message="Failed: LLM timed out",
            remediation_steps=_timeout_remediation(),
            root_cause_category="Investigation Error",
        )

    if _is_llm_cli_error(exc, "CLIInterruptedError"):
        return LLMInvokeFailure(
            user_message="Investigation was interrupted while waiting for the LLM CLI.",
            tracker_message="Failed: LLM interrupted",
            remediation_steps=["Retry the investigation when ready."],
            root_cause_category="Investigation Error",
        )

    if not isinstance(exc, RuntimeError):
        if _looks_like_timeout(exc):
            detail = str(exc).strip() or "The LLM request timed out."
            return LLMInvokeFailure(
                user_message=f"Investigation stopped: {detail}",
                tracker_message="Failed: LLM timed out",
                remediation_steps=_timeout_remediation(),
                root_cause_category="Investigation Error",
            )
        return None

    err_msg = str(exc).lower()
    raw = str(exc)

    if ("model" in err_msg and "not found" in err_msg) or "404" in err_msg:
        from core.llm.providers.azure_openai import (
            azure_deployment_not_found_remediation_steps,
            is_azure_openai_failure_message,
        )

        if "anthropic" in err_msg and "was not found" in err_msg:
            return LLMInvokeFailure(
                user_message=raw.strip()
                or "Anthropic model was not found. Check your configured model name.",
                tracker_message="Failed: Model not found",
                remediation_steps=[
                    (
                        "Verify your model name in ANTHROPIC_REASONING_MODEL or "
                        + "ANTHROPIC_TOOLCALL_MODEL environment variables."
                    ),
                    "Confirm the model ID is valid for your Anthropic account.",
                ],
            )
        if "azure openai deployment" in err_msg or is_azure_openai_failure_message(raw):
            return LLMInvokeFailure(
                user_message=raw.strip()
                or "The configured Azure OpenAI deployment was not found (404).",
                tracker_message="Failed: Azure deployment not found",
                remediation_steps=azure_deployment_not_found_remediation_steps(),
            )
        return LLMInvokeFailure(
            user_message=(
                "The configured AI model was not found (404). "
                "If using a local LLM, verify the model name in your .env file."
            ),
            tracker_message="Failed: Model not found",
            remediation_steps=[
                "Check your .env configuration",
                "Verify the model name is correct",
                "Ensure the model is downloaded locally",
                "Confirm your provider supports this model",
            ],
        )

    if "does not support tool" in err_msg or "only supports single tool" in err_msg:
        return LLMInvokeFailure(
            user_message=(
                "The configured model does not support tool calling. "
                "The investigation agent requires a model with native tool-calling support."
            ),
            tracker_message="Failed: Model does not support tools",
            remediation_steps=[
                "Switch to a model that supports tool calling (e.g. claude-opus-4-7, gpt-4o)",
                "For Ollama: use llama3.1, qwen2.5, or another tool-call-capable model",
                "Check your LLM_MODEL or LLM_PROVIDER setting in .env",
            ],
        )

    if "rate limit" in err_msg:
        return LLMInvokeFailure(
            user_message="The LLM provider rate-limited this investigation request.",
            tracker_message="Failed: LLM rate limited",
            remediation_steps=[
                "Wait a few minutes and retry.",
                "Reduce parallel load or switch to a higher quota tier if available.",
            ],
            root_cause_category="Investigation Error",
        )

    if (
        "not authenticated" in err_msg
        or "authentication" in err_msg
        or ("api key" in err_msg and "invalid" in err_msg)
    ):
        return LLMInvokeFailure(
            user_message=f"Investigation stopped: LLM authentication failed. {raw}",
            tracker_message="Failed: LLM authentication",
            remediation_steps=[
                "Verify API keys or CLI login for your LLM_PROVIDER.",
                "Run `opensre doctor` to check provider configuration.",
            ],
        )

    if _looks_like_timeout(exc):
        detail = raw.strip() or "The LLM request timed out."
        return LLMInvokeFailure(
            user_message=f"Investigation stopped: {detail}",
            tracker_message="Failed: LLM timed out",
            remediation_steps=_timeout_remediation(),
            root_cause_category="Investigation Error",
        )

    return None
