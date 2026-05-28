"""Investigation source and classification helpers for analytics events."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Final

from app.analytics.provider import Properties

INVESTIGATION_EVENT_SCHEMA_VERSION: Final[int] = 1


class EntrypointSource(StrEnum):
    """Canonical origin for an investigation invocation."""

    SDK = "sdk"
    MCP = "mcp"
    REMOTE_HTTP = "remote_http"
    CLI_COMMAND = "cli_command"
    CLI_PASTE = "cli_paste"
    CLI_REPL_FILE = "cli_repl_file"


class TriggerMode(StrEnum):
    """How the alert payload was supplied by the caller."""

    PASTE = "paste"
    FILE = "file"
    INLINE_JSON = "inline_json"
    SERVICE_RUNTIME = "service_runtime"


_API_SOURCES: Final[frozenset[EntrypointSource]] = frozenset(
    {
        EntrypointSource.SDK,
        EntrypointSource.MCP,
        EntrypointSource.REMOTE_HTTP,
    }
)


def is_test_run() -> bool:
    """Return True when the current process should be tagged as test traffic."""
    if os.getenv("OPENSRE_INVESTIGATION_SOURCE", "").strip().lower() == "test":
        return True

    if os.getenv("OPENSRE_IS_TEST", "0").strip() == "1":
        return True

    if os.getenv("PYTEST_CURRENT_TEST"):
        return True

    if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true":
        return True

    ci_value = os.getenv("CI", "").strip().lower()
    return ci_value in {"1", "true", "yes"}


def resolve_environment_tag() -> str:
    """Resolve coarse environment classification for analytics slicing."""
    raw = (
        (os.getenv("OPENSRE_ANALYTICS_ENV") or os.getenv("ENV") or os.getenv("ENVIRONMENT") or "")
        .strip()
        .lower()
    )
    if raw in {"prod", "production"}:
        return "prod"
    if raw in {"stage", "staging"}:
        return "staging"
    if raw in {"dev", "development", "local"}:
        return "dev"
    return "unknown"


def _category_for(entrypoint: EntrypointSource, *, test: bool) -> str:
    if test:
        return "test"
    if entrypoint in _API_SOURCES:
        return "api"
    return "cli"


def build_source_properties(
    *,
    entrypoint: EntrypointSource,
    trigger_mode: TriggerMode,
    investigation_id: str,
) -> Properties:
    """Return standardized source properties for investigation lifecycle events."""
    test = is_test_run()
    source = "test" if test else entrypoint.value
    return {
        "source": source,
        "entrypoint_source": entrypoint.value,
        "category": _category_for(entrypoint, test=test),
        "trigger_mode": trigger_mode.value,
        "is_test": test,
        "environment": resolve_environment_tag(),
        "investigation_id": investigation_id,
        "investigation_event_schema_version": INVESTIGATION_EVENT_SCHEMA_VERSION,
    }
