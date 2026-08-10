"""Host capability policy for gateway chat sessions.

Gateway chat never exposes the ``llm_provider`` / ``task_cancel`` tool surfaces.
``investigation`` is conditional: it is offered only where the answer can actually
be delivered back, which is what ``detached_investigations_available`` decides.

Apply this when a session is prepared for a chat transport (resolver) and again in
the turn handler for tests that inject a ``SessionCore`` directly. The resolver call
runs *before* the delivery target is bound and the turn-handler call runs inside it,
so the turn-handler call is the one that can enable ``investigation`` — which is why
the enabling branch pops the key rather than leaving the resolver's disable in place.
"""

from __future__ import annotations

import logging
from typing import Any

from gateway.core.chat import get_chat_notifier, get_current_delivery_target

logger = logging.getLogger(__name__)

UNSUPPORTED_GATEWAY_CAPABILITIES = (
    "llm_provider",
    "task_cancel",
)


def detached_investigations_available() -> bool:
    """Whether an investigation launched on this turn could be delivered back.

    True only when a delivery target is bound *and* a notifier is registered for its
    platform. Gating on the notifier rather than on a platform name is what keeps the
    synchronous pipeline unreachable from Discord and Telegram, which register none —
    and means a notifier added later switches them on with no edit here.

    A failure to answer is treated as "not available": offering a tool whose report
    has nowhere to go is the worse error. It is logged rather than swallowed, because
    silently withholding the capability looks identical to the feature being off.
    """
    try:
        target = get_current_delivery_target()
        if target is None:
            return False
        return get_chat_notifier(target.platform) is not None
    except Exception:
        logger.warning("Could not resolve chat notifier; withholding investigation", exc_info=True)
        return False


def ensure_gateway_capability_policy(session: Any) -> None:
    """Apply capability policy based on context and registered notifiers.

    Unsupported capabilities are always disabled. Investigation capability
    is conditionally available based on delivery target and notifier registration.
    """
    caps = getattr(session, "available_capabilities", None)
    if not isinstance(caps, dict):
        return

    # Always disable unsupported capabilities
    for name in UNSUPPORTED_GATEWAY_CAPABILITIES:
        caps[name] = ()

    # Conditionally handle investigation capability
    if detached_investigations_available():
        # Investigation tools are available - remove any existing disable
        caps.pop("investigation", None)
    else:
        # No notifier registered - disable investigation tools
        caps["investigation"] = ()


__all__ = [
    "UNSUPPORTED_GATEWAY_CAPABILITIES",
    "detached_investigations_available",
    "ensure_gateway_capability_policy",
]
