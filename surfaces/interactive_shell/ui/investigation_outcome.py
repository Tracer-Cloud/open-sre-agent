"""Structured investigation run outcomes for terminal UX and PostHog analytics."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from platform.common.errors import OpenSREError

InvestigationStatus = Literal["completed", "failed", "cancelled"]
FailureCategory = Literal[
    "config",
    "integration",
    "timeout",
    "user_cancelled",
    "k8s_api",
    "llm",
    "unknown",
]

_INTEGRATION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("grafana", "grafana"),
    ("loki", "grafana"),
    ("mimir", "grafana"),
    ("tempo", "grafana"),
    ("datadog", "datadog"),
    ("sentry", "sentry"),
    ("jenkins", "jenkins"),
    ("kubernetes", "k8s"),
    ("k8s", "k8s"),
    ("kubectl", "k8s"),
    ("splunk", "splunk"),
    ("honeycomb", "honeycomb"),
    ("coralogix", "coralogix"),
    ("posthog", "posthog"),
    ("github", "github"),
    ("argocd", "argocd"),
    ("pagerduty", "pagerduty"),
)


@dataclass(frozen=True, slots=True)
class InvestigationOutcome:
    """Facts from one foreground investigation run."""

    status: InvestigationStatus
    target: str = ""
    investigation_id: str = ""
    final_state: dict[str, Any] | None = None
    error_message: str = ""
    error_detail: str = ""
    failure_category: FailureCategory = "unknown"
    integration_involved: str = ""
    integration_failure_message: str = ""
    llm_model: str = ""
    llm_provider: str = ""
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    duration_ms: int = 0


def normalize_investigation_target(raw_target: str, *, path: Path | None = None) -> str:
    """Return a stable analytics slug for an investigation target."""
    if path is not None:
        return path.name or path.stem or str(path)
    stripped = raw_target.strip()
    if not stripped:
        return "investigation"
    lowered = stripped.lower()
    for prefix in ("sample:", "template:"):
        if lowered.startswith(prefix):
            return lowered[len(prefix) :].strip() or "investigation"
    if "/" in stripped or "\\" in stripped:
        return Path(stripped).name or stripped
    collapsed = re.sub(r"\s+", " ", stripped)
    if len(collapsed) > 80:
        return f"{collapsed[:77]}…"
    return collapsed


def _integration_from_message(message: str) -> tuple[str, str]:
    lowered = message.lower()
    for keyword, service in _INTEGRATION_KEYWORDS:
        if keyword in lowered:
            detail = message.strip()
            if len(detail) > 200:
                detail = f"{detail[:197]}…"
            return service, detail
    return "", ""


def classify_investigation_failure(
    exc: BaseException,
) -> tuple[FailureCategory, str, str]:
    """Map an exception to category, integration service, and integration detail."""
    message = str(exc).strip()
    if isinstance(exc, TimeoutError):
        return "timeout", "", ""
    if isinstance(exc, KeyboardInterrupt):
        return "user_cancelled", "", ""

    integration, integration_detail = _integration_from_message(message)
    lowered = message.lower()

    if integration:
        return "integration", integration, integration_detail
    if any(token in lowered for token in ("kubernetes", "k8s", "kubectl", "pod ", "deployment ")):
        return "k8s_api", "k8s", message[:200]
    if any(
        token in lowered
        for token in ("context length", "token limit", "llm", "model", "anthropic", "openai")
    ):
        return "llm", "", message[:200]
    if any(token in lowered for token in ("config", "credential", "not configured", "missing api")):
        return "config", "", message[:200]
    if isinstance(exc, OpenSREError):
        return "config", integration, integration_detail or message[:200]
    return "unknown", integration, integration_detail


def failure_detail_from_exception(exc: BaseException) -> str:
    """Best-effort truncated detail for analytics (not user-facing)."""
    return truncate_failure_detail("".join(traceback.format_exception_only(exc)).strip())


def truncate_failure_detail(text: str, *, max_chars: int = 500) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 20].rstrip()}… [truncated]"


def user_facing_error_message(exc: BaseException, *, max_lines: int = 3) -> str:
    """Compact user-facing error text for analytics payloads."""
    if isinstance(exc, OpenSREError):
        parts = [exc.message.strip()]
        if exc.suggestion:
            parts.append(f"Suggestion: {exc.suggestion.strip()}")
        text = "\n".join(part for part in parts if part)
    else:
        text = str(exc).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return type(exc).__name__
    return "\n".join(lines[:max_lines])


__all__ = [
    "FailureCategory",
    "InvestigationOutcome",
    "InvestigationStatus",
    "classify_investigation_failure",
    "failure_detail_from_exception",
    "normalize_investigation_target",
    "truncate_failure_detail",
    "user_facing_error_message",
]
