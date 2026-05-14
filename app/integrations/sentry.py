"""Shared Sentry integration helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_SENTRY_URL = "https://sentry.io"
DEFAULT_SENTRY_STATS_PERIOD = "24h"
_EXCEPTION_QUERY_PREFIX_RE = re.compile(
    r"^(?:[A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Warning)|panic|error|runtime error|fatal error):\s*"
)
_BARE_EXCEPTION_QUERY_PREFIX_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Warning)|panic|error|runtime error|fatal error):\s*$"
)
_BARE_EXCEPTION_QUERY_NAME_RE = re.compile(
    r"^(?:[A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Warning)|panic|error|runtime error|fatal error)$"
)
_STRUCTURED_QUERY_KEY_RE = re.compile(r"(?<!\S)!?(?P<key>[A-Za-z][A-Za-z0-9_.-]*):")
_STRUCTURED_QUERY_KEYS = frozenset(
    {
        "age",
        "assigned",
        "browser.name",
        "browser.version",
        "culprit",
        "device.family",
        "device.model",
        "dist",
        "environment",
        "error.handled",
        "error.type",
        "error.unhandled",
        "event.type",
        "firstSeen",
        "has",
        "is",
        "issue.category",
        "issue.type",
        "lastSeen",
        "level",
        "logger",
        "message",
        "os.name",
        "os.version",
        "project",
        "release",
        "server_name",
        "stack.filename",
        "stack.function",
        "status",
        "timesSeen",
        "title",
        "transaction",
        "url",
        "user",
        "user.email",
        "user.id",
        "user.ip",
    }
)


class SentryConfig(StrictConfigModel):
    """Normalized Sentry connection settings."""

    base_url: str = DEFAULT_SENTRY_URL
    organization_slug: str = ""
    auth_token: str = ""
    project_slug: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_SENTRY_URL).strip()
        return normalized or DEFAULT_SENTRY_URL

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class SentryValidationResult:
    """Result of validating a Sentry integration."""

    ok: bool
    detail: str
    issue_count: int = 0


def build_sentry_config(raw: dict[str, Any] | None) -> SentryConfig:
    """Build a normalized Sentry config object from env/store data."""
    return SentryConfig.model_validate(raw or {})


def sentry_config_from_env() -> SentryConfig | None:
    """Load a Sentry config from env vars."""
    organization_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if not organization_slug or not auth_token:
        return None
    return build_sentry_config(
        {
            "base_url": os.getenv("SENTRY_URL", DEFAULT_SENTRY_URL).strip() or DEFAULT_SENTRY_URL,
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
        }
    )


def get_sentry_auth_recommendations() -> dict[str, str]:
    """Return operator guidance for creating the right Sentry token."""
    return {
        "recommended_token_type": "Organization Token",
        "why": (
            "Use an Organization Token first for least-privilege automation. "
            "Use an Internal Integration only if you need broader organization-level API scopes."
        ),
        "where_to_create": "Settings > Developer Settings > Organization Tokens",
        "fallback_token_type": "Internal Integration",
        "fallback_where_to_create": "Settings > Developer Settings > Internal Integrations",
        "required_scope_hint": "Issue and event lookup requires an auth token with event:read access.",
    }


def _build_issue_list_params(
    config: SentryConfig,
    limit: int,
    query: str,
) -> list[tuple[str, str | int | float | bool | None]]:
    normalized_query = _normalize_issue_search_query(query)
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("limit", str(limit)),
        ("statsPeriod", DEFAULT_SENTRY_STATS_PERIOD),
        ("query", normalized_query),
    ]
    if config.project_slug:
        params.append(("project", config.project_slug))
    return params


def _normalize_issue_search_query(query: str) -> str:
    stripped = query.strip()
    if not stripped or _is_quoted_search_query(stripped):
        return stripped

    free_text, leading_filters, trailing_filters = _split_structured_query_filters(stripped)
    if (leading_filters or trailing_filters) and not free_text:
        return stripped
    if leading_filters or trailing_filters:
        normalized_free_text = (
            _quote_search_phrase(free_text)
            if not _is_quoted_search_query(free_text)
            and (_starts_like_exception_signature(free_text) or _is_bare_exception_name(free_text))
            else free_text
        )
        return " ".join([*leading_filters, normalized_free_text, *trailing_filters])
    if _starts_like_exception_signature(stripped):
        return _quote_search_phrase(stripped)
    return stripped


def _is_quoted_search_query(query: str) -> bool:
    return len(query) >= 2 and query[0] == '"' and query[-1] == '"'


def _starts_like_exception_signature(query: str) -> bool:
    return bool(_EXCEPTION_QUERY_PREFIX_RE.search(query))


def _is_bare_exception_name(query: str) -> bool:
    return bool(_BARE_EXCEPTION_QUERY_NAME_RE.fullmatch(query))


def _split_structured_query_filters(query: str) -> tuple[str, list[str], list[str]]:
    tokens = _structured_query_tokens(query)
    if not tokens:
        return query, [], []

    leading_filters: list[str] = []
    prefix_end = 0
    token_index = 0

    while token_index < len(tokens):
        start, end, text = tokens[token_index]
        if query[prefix_end:start].strip():
            break
        leading_filters.append(text)
        prefix_end = end
        token_index += 1

    trailing_filters: list[str] = []
    suffix_start = len(query)
    token_index = len(tokens) - 1

    while token_index >= len(leading_filters):
        start, end, text = tokens[token_index]
        if query[end:suffix_start].strip():
            break
        trailing_filters.insert(0, text)
        suffix_start = start
        token_index -= 1

    if not leading_filters and not trailing_filters:
        return query, [], []

    free_text = query[prefix_end:suffix_start].strip()
    if not free_text:
        return "", leading_filters, trailing_filters

    bare_exception_prefix = _BARE_EXCEPTION_QUERY_PREFIX_RE.fullmatch(free_text)
    if bare_exception_prefix:
        free_text = bare_exception_prefix.group("name")

    return free_text, leading_filters, trailing_filters


def _structured_query_tokens(query: str) -> list[tuple[int, int, str]]:
    tokens: list[tuple[int, int, str]] = []
    for match in _STRUCTURED_QUERY_KEY_RE.finditer(query):
        if match.group("key") not in _STRUCTURED_QUERY_KEYS:
            continue
        value_start = match.end()
        if value_start >= len(query) or query[value_start].isspace():
            continue
        end = _structured_query_value_end(query, value_start)
        if end is None:
            continue
        tokens.append((match.start(), end, query[match.start() : end]))
    return tokens


def _structured_query_value_end(query: str, value_start: int) -> int | None:
    quote = query[value_start]
    if quote in {'"', "'"}:
        index = value_start + 1
        escaped = False
        while index < len(query):
            char = query[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                return index + 1
            index += 1
        return None

    index = value_start
    while index < len(query) and not query[index].isspace():
        index += 1
    return index


def _quote_search_phrase(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _request_json(
    config: SentryConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        headers=config.auth_headers,
        params=params,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_sentry_config(config: SentryConfig) -> SentryValidationResult:
    """Validate Sentry connectivity with a lightweight issues query."""

    if not config.organization_slug:
        return SentryValidationResult(ok=False, detail="Sentry organization slug is required.")
    if not config.auth_token:
        return SentryValidationResult(ok=False, detail="Sentry auth token is required.")

    try:
        issues = list_sentry_issues(config=config, limit=1)
        issue_count = len(issues)
        return SentryValidationResult(
            ok=True,
            detail=(
                f"Sentry validated for org {config.organization_slug}; "
                f"issues API responded successfully with {issue_count} issue(s)."
            ),
            issue_count=issue_count,
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {detail}")
    except Exception as err:
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {err}")


def list_sentry_issues(
    *,
    config: SentryConfig,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List Sentry issues for an organization."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/",
        params=_build_issue_list_params(config, limit, query),
    )
    return payload if isinstance(payload, list) else []


def get_sentry_issue(
    *,
    config: SentryConfig,
    issue_id: str,
) -> dict[str, Any]:
    """Fetch full details for one Sentry issue."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/{issue_id}/",
    )
    return payload if isinstance(payload, dict) else {}


def list_sentry_issue_events(
    *,
    config: SentryConfig,
    issue_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List recent events for a Sentry issue."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/{issue_id}/events/",
        params=[("limit", str(limit))],
    )
    return payload if isinstance(payload, list) else []
