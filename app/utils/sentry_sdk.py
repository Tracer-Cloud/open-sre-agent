"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry using the project DSN constant.  Call ``init_sentry()`` once
early in each process entry-point (CLI, LangGraph worker, etc.).  Repeated calls
are safe — the function is idempotent.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from functools import cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.analytics.events import Event
from app.constants import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_TRACES_SAMPLE_RATE,
)

_HOME_PATH_RE: re.Pattern[str] = re.compile(r"/(?:Users|home)/[^/\s]+")
_SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = ("_token", "_key", "_secret", "_password")
_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "prompt",
    "messages",
    "system_prompt",
    "dsn",
    "bearer",
    "cookie",
    "auth",
    "credential",
)
_QUERY_SCRUBBING_CATEGORIES: frozenset[str] = frozenset({"http", "httpx", "aiohttp"})
_HOSTED_ENTRYPOINTS: frozenset[str] = frozenset({"webapp", "remote", "mcp"})


def _is_sentry_disabled() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv("OPENSRE_SENTRY_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _sample_rate_from_env(env_var: str, default: float) -> float:
    try:
        sample_rate = float(os.getenv(env_var, str(default)))
    except ValueError:
        return default
    return min(1.0, max(0.0, sample_rate))


def _resolved_dsn() -> str:
    """Allow env overrides while keeping the bundled DSN as the default."""
    return os.getenv("OPENSRE_SENTRY_DSN") or os.getenv("SENTRY_DSN") or SENTRY_DSN


def _scrub_string(value: object) -> object:
    if isinstance(value, str):
        return _HOME_PATH_RE.sub("~", value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if any(lowered.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES):
        return True
    return any(token in lowered for token in _SENSITIVE_KEY_SUBSTRINGS)


def _scrub_mapping_in_place(payload: dict[str, Any]) -> None:
    """Recursively redact sensitive keys inside a request body/data mapping."""
    for key, value in list(payload.items()):
        if _is_sensitive_key(key):
            payload[key] = "[Filtered]"
            continue
        if isinstance(value, dict):
            _scrub_mapping_in_place(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _scrub_mapping_in_place(item)


def _scrub_request(request: dict[str, Any]) -> None:
    headers = request.get("headers")
    if isinstance(headers, dict):
        for header in list(headers):
            if header.lower() in {"authorization", "cookie", "set-cookie", "x-api-key"}:
                headers[header] = "[Filtered]"
    if "cookies" in request:
        request["cookies"] = "[Filtered]"
    for body_key in ("data", "body"):
        body = request.get(body_key)
        if isinstance(body, dict):
            _scrub_mapping_in_place(body)


def _scrub_extra(extra: dict[str, Any]) -> None:
    for key in list(extra):
        if _is_sensitive_key(key):
            extra[key] = "[Filtered]"


def _scrub_stacktrace_frames(frames: list[dict[str, Any]]) -> None:
    for frame in frames:
        for path_key in ("abs_path", "filename"):
            if path_key in frame:
                frame[path_key] = _scrub_string(frame[path_key])
        local_vars = frame.get("vars")
        if isinstance(local_vars, dict):
            for key, value in list(local_vars.items()):
                if _is_sensitive_key(key):
                    local_vars[key] = "[Filtered]"
                else:
                    local_vars[key] = _scrub_string(value)


def _scrub_event_in_place(event: dict[str, Any]) -> None:
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)

    extra = event.get("extra")
    if isinstance(extra, dict):
        _scrub_extra(extra)

    exception = event.get("exception")
    if isinstance(exception, dict):
        for entry in exception.get("values", []) or []:
            stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
            if isinstance(stacktrace, dict):
                frames = stacktrace.get("frames")
                if isinstance(frames, list):
                    _scrub_stacktrace_frames(frames)


def _before_send(event: Any, _hint: dict[str, Any]) -> Any:
    """Drop or scrub a Sentry event before transport.

    Returns ``None`` to drop the event (e.g. when DSN is empty), otherwise
    returns the same dict with sensitive bits replaced with ``[Filtered]``.
    """
    if not _resolved_dsn():
        return None
    if not isinstance(event, dict):
        return event
    try:
        _scrub_event_in_place(event)
    except Exception:  # noqa: BLE001
        # The hook must never raise — Sentry will swallow the event silently.
        return event
    return event


def _strip_url_query(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _before_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip query strings and header sub-dicts from HTTP breadcrumbs."""
    category = crumb.get("category")
    if isinstance(category, str) and category in _QUERY_SCRUBBING_CATEGORIES:
        data = crumb.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str):
                data["url"] = _strip_url_query(url)
            if "headers" in data:
                data["headers"] = "[Filtered]"
    return crumb


def _capture_sentry_init_skipped(reason: str, *, error_type: str | None = None) -> None:
    # Local import to avoid an import cycle between Sentry and analytics modules.
    from app.analytics.provider import Properties, get_analytics

    properties: Properties = {"reason": reason}
    if error_type is not None:
        properties["error_type"] = error_type
    with suppress(Exception):
        get_analytics().capture(Event.SENTRY_INIT_SKIPPED, properties)


def _build_integrations() -> list[Any]:
    """Build the explicit Sentry integrations list.

    ``LoggingIntegration(event_level=ERROR)`` promotes existing
    ``logger.error`` / ``logger.exception`` calls into Sentry events without
    touching call sites. AsyncIO and HTTPX integrations are pinned so behavior
    stays deterministic across SDK versions.
    """
    integrations: list[Any] = []
    try:
        from sentry_sdk.integrations.logging import LoggingIntegration

        integrations.append(LoggingIntegration(level=logging.INFO, event_level=logging.ERROR))
    except Exception:  # noqa: BLE001
        pass
    try:
        from sentry_sdk.integrations.asyncio import AsyncioIntegration

        integrations.append(AsyncioIntegration())
    except Exception:  # noqa: BLE001
        pass
    try:
        from sentry_sdk.integrations.httpx import HttpxIntegration

        integrations.append(HttpxIntegration())
    except Exception:  # noqa: BLE001
        pass
    return integrations


def _detect_deployment_method() -> str | None:
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        return "railway"
    if os.getenv("LANGGRAPH_HOSTED") or os.getenv("LANGSMITH_DEPLOYMENT_ID"):
        return "langsmith"
    return "local"


def _runtime_for(entrypoint: str | None) -> str:
    return "hosted" if entrypoint in _HOSTED_ENTRYPOINTS else "cli"


@cache
def _init_sentry_once(
    dsn: str,
    environment: str,
    release: str,
    sample_rate: float,
    traces_sample_rate: float,
) -> None:
    """Initialize Sentry once per effective runtime configuration."""
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        attach_stacktrace=True,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
        max_breadcrumbs=100,
        in_app_include=["app"],
        integrations=_build_integrations(),
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )


def _apply_scope_tags(entrypoint: str | None) -> None:
    """Set per-process scope tags. Must run outside the cached init.

    ``_init_sentry_once`` is ``@cache``-d on its kwargs, so any scope mutation
    placed inside it would be skipped on cache hits. Tags only become visible
    on subsequent events when set on the global scope here.
    """
    with suppress(Exception):
        import sentry_sdk

        if entrypoint is not None:
            sentry_sdk.set_tag("entrypoint", entrypoint)
        sentry_sdk.set_tag("runtime", _runtime_for(entrypoint))
        deployment_method = _detect_deployment_method()
        if deployment_method is not None:
            sentry_sdk.set_tag("deployment_method", deployment_method)


def init_sentry(entrypoint: str | None = None) -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    DSN sourcing precedence: ``OPENSRE_SENTRY_DSN`` env var, ``SENTRY_DSN``
    env var, then the bundled constant. Set ``OPENSRE_NO_TELEMETRY=1`` or
    ``DO_NOT_TRACK=1`` to disable both Sentry and PostHog product analytics.
    ``OPENSRE_SENTRY_DISABLED=1`` disables Sentry only;
    ``OPENSRE_ANALYTICS_DISABLED=1`` disables PostHog only.

    Pass ``entrypoint`` to tag every Sentry event with the surface that
    initialised the SDK (e.g. ``cli``, ``webapp``, ``remote``). The tag is
    applied on the global scope, not inside the cached ``_init_sentry_once``,
    so it survives across cache hits.
    """
    if _is_sentry_disabled():
        _capture_sentry_init_skipped("telemetry_disabled")
        return

    from app.config import get_environment
    from app.version import get_version

    try:
        _init_sentry_once(
            dsn=_resolved_dsn(),
            environment=get_environment().value,
            release=f"opensre@{get_version()}",
            sample_rate=_sample_rate_from_env(
                "SENTRY_ERROR_SAMPLE_RATE",
                SENTRY_ERROR_SAMPLE_RATE,
            ),
            traces_sample_rate=_sample_rate_from_env(
                "SENTRY_TRACES_SAMPLE_RATE",
                SENTRY_TRACES_SAMPLE_RATE,
            ),
        )
    except ModuleNotFoundError:
        _capture_sentry_init_skipped("missing_sdk", error_type="ModuleNotFoundError")
        raise
    except Exception as exc:
        _capture_sentry_init_skipped("init_error", error_type=type(exc).__name__)
        raise

    _apply_scope_tags(entrypoint)


def capture_exception(
    exc: BaseException,
    *,
    context: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Best-effort capture for exceptions swallowed by boundary adapters."""
    if _is_sentry_disabled():
        return
    with suppress(Exception):
        import sentry_sdk

        if context is None and not extra:
            sentry_sdk.capture_exception(exc)
            return
        with sentry_sdk.push_scope() as scope:
            if context is not None:
                scope.set_tag("opensre.context", context)
            if extra:
                for key, value in extra.items():
                    scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)
