"""Transport-neutral attachment text rendering for gateway turns."""

from __future__ import annotations

import re

_MAX_FILE_CHARS = 40_000
_MAX_TOTAL_CHARS = 120_000
_HEAD_CHARS = 26_000
_TAIL_CHARS = 12_000

_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/csv",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/x-sh",
    }
)

_SECRET_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.DOTALL
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)(\s*[=:]\s*)\S{6,}"),
        r"\1\2[REDACTED]",
    ),
)


def scrub_secrets(text: str) -> str:
    """Redact obvious credentials from text before it reaches the model."""
    for pattern, replacement in _SECRET_SUBS:
        text = pattern.sub(replacement, text)
    return text


def is_text_mimetype(mimetype: str) -> bool:
    mime = mimetype.split(";", 1)[0].strip().lower()
    return mime.startswith("text/") or mime in _TEXT_MIME_EXACT


def truncate_attachment_text(text: str, *, max_chars: int = _MAX_FILE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return f"{text[:_HEAD_CHARS]}\n… [{omitted} characters omitted] …\n{text[-_TAIL_CHARS:]}"


def budgeted_section(header: str, raw_text: str, remaining: int) -> tuple[str, int]:
    """Scrub and truncate ``raw_text`` to the remaining budget under ``header``."""
    body = scrub_secrets(raw_text)[:remaining]
    return f"{header}\n{body}", len(body)


def join_attachment_sections(sections: list[str]) -> str:
    if not sections:
        return ""
    return "Attached files:\n" + "\n".join(sections)


__all__ = [
    "_MAX_FILE_CHARS",
    "_MAX_TOTAL_CHARS",
    "budgeted_section",
    "is_text_mimetype",
    "join_attachment_sections",
    "scrub_secrets",
    "truncate_attachment_text",
]
