"""Applies the optional :class:`~core.provider.ProviderHooks` around each LLM
call, and swallows a hook's error instead of letting it break the loop.

``Agent`` owns one :class:`ProviderHookDelegate` per run. The loop
(``core.agent.react_loop.run_react_loop``) calls it at four points around each
request — transform the messages, convert them to the provider format, adjust
the outgoing request, adjust the incoming response — instead of touching
``ProviderHooks`` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.messages import MessageFormatter
from core.provider import ProviderHooks, ProviderRequest

if TYPE_CHECKING:
    from core.messages import ProviderMessage, RuntimeMessage

logger = logging.getLogger(__name__)


@dataclass
class ProviderHookDelegate:
    """Wraps :class:`ProviderHooks`; swallows hook exceptions and logs instead."""

    hooks: ProviderHooks

    def transform_messages(self, messages: Sequence[RuntimeMessage]) -> list[RuntimeMessage]:
        try:
            return self.hooks.apply_transform_messages(messages)
        except Exception:  # noqa: BLE001 - fall back to the unmodified transcript
            logger.debug(
                "[runtime] transform_messages raised; using original messages", exc_info=True
            )
            return list(messages)

    def convert_to_llm(self, llm: Any, messages: Sequence[RuntimeMessage]) -> list[ProviderMessage]:
        try:
            return self.hooks.apply_convert_to_llm(llm, messages)
        except Exception:  # noqa: BLE001 - fall back to the standard provider conversion
            logger.debug("[runtime] convert_to_llm raised; using default conversion", exc_info=True)
            return MessageFormatter(llm).to_provider_messages(messages)

    def before_request(self, request: ProviderRequest) -> ProviderRequest:
        try:
            return self.hooks.apply_before_request(request)
        except Exception:  # noqa: BLE001 - provider hooks are observability/customization only
            logger.debug("[runtime] before_provider_request raised; ignoring", exc_info=True)
            return request

    def after_response(self, request: ProviderRequest, response: Any) -> Any:
        try:
            return self.hooks.apply_after_response(request, response)
        except Exception:  # noqa: BLE001 - preserve the transcript if hooks fail
            logger.debug("[runtime] after_provider_response raised; ignoring", exc_info=True)
            return response


__all__ = ["ProviderHookDelegate"]
