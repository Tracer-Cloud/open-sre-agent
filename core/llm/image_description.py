"""One-shot image → text description via a vision-capable model.

Lets text-only surfaces (e.g. the Slack gateway) support image attachments
without threading image content blocks through the whole turn pipeline: the
image is described once here and the description is inlined as plain text.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SUPPORTED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_VISION_MAX_TOKENS = 1024
# Explicit cap so an image attachment can't stall a gateway turn on the SDK's
# ~60s default; the caller degrades to a "could not describe" line on timeout.
_VISION_TIMEOUT_SECONDS = 20.0
_VISION_PROMPT = (
    "You are assisting an SRE. Describe this image concisely and extract any text, "
    "error messages, metric values, timestamps, and what it depicts. Be factual and "
    "do not speculate beyond what is visible."
)


def is_supported_image(mimetype: str) -> bool:
    """Whether an image MIME type can be described by the vision model."""
    return mimetype.split(";", 1)[0].strip().lower() in _SUPPORTED_IMAGE_MIMES


def describe_image(
    image_bytes: bytes,
    mimetype: str,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> str | None:
    """Return a text description of an image, or None on any failure.

    ``client`` is injectable so callers/tests can supply a fake; production
    builds an Anthropic client from ``ANTHROPIC_API_KEY``.
    """
    mime = mimetype.split(";", 1)[0].strip().lower()
    if mime not in _SUPPORTED_IMAGE_MIMES or not image_bytes:
        return None
    try:
        if client is None:
            from anthropic import Anthropic

            from config.llm_credentials import resolve_env_credential

            client = Anthropic(
                api_key=resolve_env_credential("ANTHROPIC_API_KEY"),
                timeout=_VISION_TIMEOUT_SECONDS,
            )
        if model is None:
            from config.config import ANTHROPIC_TOOLCALL_MODEL

            model = ANTHROPIC_TOOLCALL_MODEL
        # Raw block dicts (the Anthropic SDK's typed param union is stricter than
        # the wire format it accepts), so the payload is Any-typed.
        messages: Any = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                ],
            }
        ]
        response = client.messages.create(
            model=model, max_tokens=_VISION_MAX_TOKENS, messages=messages
        )
    except Exception as exc:  # noqa: BLE001 - any provider/transport failure degrades to None
        logger.warning("[vision] describe_image failed: %s", type(exc).__name__)
        return None

    parts = [
        block.text
        for block in getattr(response, "content", [])
        if getattr(block, "type", "") == "text" and getattr(block, "text", "")
    ]
    return "\n".join(parts).strip() or None
