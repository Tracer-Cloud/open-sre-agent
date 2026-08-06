"""Download and inline files shared in Slack messages into the turn prompt.

Text-like files (logs, CSV, JSON, YAML) are decoded and inlined with obvious
secrets scrubbed; images are described once by a vision model and the
description is inlined. Other binaries are named only. This keeps the turn
pipeline text-only while still surfacing attachment content.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

from core.llm.image_description import describe_image_via_provider, is_supported_image
from gateway.transports.slack.events import SlackInboundFile

logger = logging.getLogger(__name__)

# Text-like MIME types we inline into the turn (in addition to any ``text/*``).
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

_MAX_FILE_BYTES = 256 * 1024  # per-file download ceiling
_MAX_FILE_CHARS = 40_000  # per-file inlined characters (~10k tokens)
_MAX_TOTAL_CHARS = 120_000  # inlined characters across all files in one turn
_HEAD_CHARS = 26_000
_TAIL_CHARS = 12_000
_DOWNLOAD_TIMEOUT_SECONDS = 10.0

# Downloads a file's bytes given its metadata + bot token; None on any failure.
Downloader = Callable[[SlackInboundFile, str], "bytes | None"]
# Describes an image's bytes (mimetype) as text; None when it cannot.
Describer = Callable[[bytes, str], "str | None"]

# High-confidence secret patterns scrubbed from inlined content before it reaches
# the model. Deliberately narrow — infra identifiers (pods, services) are left
# intact because an SRE needs them to diagnose; only obvious credentials go.
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


def _scrub_secrets(text: str) -> str:
    """Redact obvious credentials from text before it reaches the model."""
    for pattern, replacement in _SECRET_SUBS:
        text = pattern.sub(replacement, text)
    return text


def is_text_file(file: SlackInboundFile) -> bool:
    """Whether a file's content can be inlined as text into the turn."""
    mime = file.mimetype.split(";", 1)[0].strip().lower()
    return mime.startswith("text/") or mime in _TEXT_MIME_EXACT


def _truncate(text: str) -> str:
    """Keep the head and tail of oversized text (start context + latest errors)."""
    if len(text) <= _MAX_FILE_CHARS:
        return text
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return f"{text[:_HEAD_CHARS]}\n… [{omitted} characters omitted] …\n{text[-_TAIL_CHARS:]}"


def extract_text(file: SlackInboundFile, data: bytes) -> str | None:
    """Decode a downloaded text-like file, truncating oversized content."""
    if not is_text_file(file):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return _truncate(text)

_ALLOWED_SLACK_HOST_SUFFIXES = (".slack.com", ".slack-edge.com")
_ALLOWED_SLACK_HOSTS = frozenset(
    {"files.slack.com", "slack-files.com", "downloads.slack-edge.com", "slack-edge.com"}
)
_MAX_REDIRECTS = 5


def _is_allowed_slack_host(host: str) -> bool:
    host = host.lower()
    return host in _ALLOWED_SLACK_HOSTS or any(
        host.endswith(suffix) for suffix in _ALLOWED_SLACK_HOST_SUFFIXES
    )


def download_file(file: SlackInboundFile, token: str) -> bytes | None:
    """GET a Slack ``url_private`` with the bot token; None on any failure.

    Slack redirects file downloads (307/308) and requires the bot token as a
    bearer header. Redirect target hosts are validated against the Slack host
    allowlist, and the Authorization header is never forwarded across different
    hosts. The response is size-capped so a huge upload cannot exhaust memory.
    """
    if not (file.url_private and token):
        return None

    current_url = file.url_private
    initial_parsed = urlparse(current_url)
    initial_host = (initial_parsed.hostname or "").lower()

    if not _is_allowed_slack_host(initial_host):
        logger.warning("[slack-files] initial host %s not on allowlist; rejected", initial_host)
        return None

    try:
        with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS):
                parsed = urlparse(current_url)
                host = (parsed.hostname or "").lower()

                if not _is_allowed_slack_host(host):
                    logger.warning(
                        "[slack-files] redirect target host %s not on allowlist; rejected", host
                    )
                    return None

                headers: dict[str, str] = {}
                if host == initial_host or host == "files.slack.com":
                    headers["Authorization"] = f"Bearer {token}"

                with client.stream("GET", current_url, headers=headers) as response:
                    if response.status_code in (
                        httpx.codes.MOVED_PERMANENTLY,
                        httpx.codes.FOUND,
                        httpx.codes.SEE_OTHER,
                        httpx.codes.TEMPORARY_REDIRECT,
                        httpx.codes.PERMANENT_REDIRECT,
                    ):
                        location = response.headers.get("location")
                        if not location:
                            return None
                        current_url = urljoin(current_url, location)
                        continue

                    if response.status_code != httpx.codes.OK:
                        logger.warning(
                            "[slack-files] %s download HTTP %s", file.name, response.status_code
                        )
                        return None

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > _MAX_FILE_BYTES:
                            logger.info(
                                "[slack-files] %s exceeds %d bytes; skipped",
                                file.name,
                                _MAX_FILE_BYTES,
                            )
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks)
            logger.warning("[slack-files] too many redirects downloading %s", file.name)
            return None
    except httpx.HTTPError as exc:
        logger.warning("[slack-files] %s download failed: %s", file.name, type(exc).__name__)
        return None


def _budgeted_section(header: str, raw_text: str, remaining: int) -> tuple[str, int]:
    """Scrub and truncate ``raw_text`` to the remaining budget under ``header``.

    Returns the rendered section and the character count it consumed.
    """
    body = _scrub_secrets(raw_text)[:remaining]
    return f"{header}\n{body}", len(body)


def _render_file(
    file: SlackInboundFile,
    token: str,
    remaining: int,
    *,
    downloader: Downloader,
    describer: Describer,
) -> tuple[str, int]:
    """Render one attachment as a prompt section plus the characters it consumed.

    Unreadable files and every failure path render as a one-line note that costs
    no budget; text files and described images render their scrubbed body against
    ``remaining``.
    """
    label = file.name or file.id
    readable_text = is_text_file(file)
    image = is_supported_image(file.mimetype)
    if not (readable_text or image):
        return f"- {label} ({file.mimetype or 'binary'}) — not readable", 0
    if remaining <= 0:
        return f"- {label} — omitted (attachment budget exhausted)", 0
    data = downloader(file, token)
    if data is None:
        return f"- {label} — could not be downloaded", 0
    if readable_text:
        header = f"--- attached file: {label} ---"
        return _budgeted_section(header, extract_text(file, data) or "", remaining)
    description = describer(data, file.mimetype)
    if not description:
        return f"- {label} ({file.mimetype}) — image could not be described", 0
    header = f"--- image: {label} (vision description) ---"
    return _budgeted_section(header, description, remaining)


def build_files_context(
    files: tuple[SlackInboundFile, ...],
    token: str,
    *,
    downloader: Downloader = download_file,
    describer: Describer = describe_image_via_provider,
) -> str:
    """Render shared files as a text block appended to the turn prompt.

    Text-like files are inlined (secrets scrubbed); images are described by a
    vision model and the description is inlined; other binaries are named only.
    Per-file and total character caps bound the added context. Returns an empty
    string when there is nothing to add.
    """
    if not files:
        return ""
    sections: list[str] = []
    remaining = _MAX_TOTAL_CHARS
    for file in files:
        section, consumed = _render_file(
            file, token, remaining, downloader=downloader, describer=describer
        )
        remaining -= consumed
        sections.append(section)
    return "Attached files:\n" + "\n".join(sections)
