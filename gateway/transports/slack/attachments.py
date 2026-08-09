"""Download and inline files shared in Slack messages into the turn prompt.

Text-like files (logs, CSV, JSON, YAML) are decoded and inlined with obvious
secrets scrubbed; images are described once by a vision model and the
description is inlined. Other binaries are named only. This keeps the turn
pipeline text-only while still surfacing attachment content.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from config.constants.gateway import ATTACHMENT_MAX_TOTAL_CHARS
from core.llm.image_description import describe_image_via_provider, is_supported_image
from gateway.core.attachments.inline import (
    budgeted_section,
    is_text_mimetype,
    join_attachment_sections,
    truncate_attachment_text,
)
from gateway.transports.slack.events import SlackInboundFile

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 256 * 1024  # per-file download ceiling
_DOWNLOAD_TIMEOUT_SECONDS = 10.0

# Downloads a file's bytes given its metadata + bot token; None on any failure.
Downloader = Callable[[SlackInboundFile, str], "bytes | None"]
# Describes an image's bytes (mimetype) as text; None when it cannot.
Describer = Callable[[bytes, str], "str | None"]


def is_text_file(file: SlackInboundFile) -> bool:
    """Whether a file's content can be inlined as text into the turn."""
    return is_text_mimetype(file.mimetype)


def extract_text(file: SlackInboundFile, data: bytes) -> str | None:
    """Decode a downloaded text-like file, truncating oversized content."""
    if not is_text_file(file):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return truncate_attachment_text(text)


def download_file(file: SlackInboundFile, token: str) -> bytes | None:
    """GET a Slack ``url_private`` with the bot token; None on any failure.

    Slack redirects file downloads (307/308) and requires the bot token as a
    bearer header. The response is size-capped so a huge upload cannot exhaust
    memory (the file is dropped, not truncated, past the cap).
    """
    if not (file.url_private and token):
        return None
    try:
        with httpx.stream(
            "GET",
            file.url_private,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
        ) as response:
            if response.status_code != httpx.codes.OK:
                logger.warning("[slack-files] %s download HTTP %s", file.name, response.status_code)
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > _MAX_FILE_BYTES:
                    logger.info(
                        "[slack-files] %s exceeds %d bytes; skipped", file.name, _MAX_FILE_BYTES
                    )
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        logger.warning("[slack-files] %s download failed: %s", file.name, type(exc).__name__)
        return None


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
        return budgeted_section(header, extract_text(file, data) or "", remaining)
    description = describer(data, file.mimetype)
    if not description:
        return f"- {label} ({file.mimetype}) — image could not be described", 0
    header = f"--- image: {label} (vision description) ---"
    return budgeted_section(header, description, remaining)


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
    remaining = ATTACHMENT_MAX_TOTAL_CHARS
    for file in files:
        section, consumed = _render_file(
            file, token, remaining, downloader=downloader, describer=describer
        )
        remaining -= consumed
        sections.append(section)
    return join_attachment_sections(sections)
