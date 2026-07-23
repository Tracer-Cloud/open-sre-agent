"""One-shot image → text description via the configured LLM provider.

Lets text-only surfaces (e.g. the Slack gateway) support image attachments
without threading image content blocks through the whole turn pipeline: the
image is described once here and the description is inlined as plain text.

The call is routed through the provider that ``get_llm`` resolved — each
vision-capable client (Anthropic, OpenAI-family) implements ``describe_image``
with its own image-block format. Providers without vision support degrade to
``None`` (the caller names the file but does not inline a description).
"""

from __future__ import annotations

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


def _configured_agent() -> Any:
    """Return the agent client for the configured LLM provider."""
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.AGENT)


def describe_image_via_provider(
    image_bytes: bytes,
    mimetype: str,
    *,
    agent: Any | None = None,
) -> str | None:
    """Describe an image via the configured provider's vision model, or ``None``.

    ``agent`` is injectable for tests; production resolves the configured agent
    client via ``get_llm``. Returns ``None`` for unsupported images, providers
    without a ``describe_image`` capability, or any transport/provider failure.
    """
    mime = mimetype.split(";", 1)[0].strip().lower()
    if mime not in _SUPPORTED_IMAGE_MIMES or not image_bytes:
        return None
    try:
        if agent is None:
            agent = _configured_agent()
        describe = getattr(agent, "describe_image", None)
        if not callable(describe):
            logger.info("[vision] configured LLM provider has no image support")
            return None
        description: str | None = describe(
            image_bytes,
            mime,
            prompt=_VISION_PROMPT,
            max_tokens=_VISION_MAX_TOKENS,
            timeout=_VISION_TIMEOUT_SECONDS,
        )
        return description
    except Exception as exc:  # noqa: BLE001 - any provider/transport failure degrades to None
        logger.warning("[vision] describe_image failed: %s", type(exc).__name__)
        return None
