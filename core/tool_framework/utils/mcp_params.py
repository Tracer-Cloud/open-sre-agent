"""Normalize MCP integration source dicts for tool param extraction.

MCP-backed tools read connection settings from the verified integration
source (e.g. ``posthog_mcp``, ``sentry_mcp``, ``openclaw``). Catalog and
runtime configs may use prefixed keys (``posthog_url``) or short aliases
(``url``). These helpers pick the first non-empty value across alias keys
and coerce list fields into stripped string lists.
"""

from __future__ import annotations

__all__ = ["first_list", "first_string", "string_list"]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    # An explicitly-None item (a normal value for an unset optional
    # field) must be dropped, not coerced to the truthy string "None"
    # that would be injected into MCP CLI args or environment values.
    return [text for item in value if item is not None for text in [str(item).strip()] if text]


def first_string(source: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        # An explicitly-None value (a normal value for an unset optional
        # field) must fall through to the next alias key, not become the
        # truthy string "None" that would be injected as a token/URL.
        value = source.get(key, "")
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def first_list(source: dict[str, object], *keys: str) -> list[str]:
    for key in keys:
        values = string_list(source.get(key, []))
        if values:
            return values
    return []
