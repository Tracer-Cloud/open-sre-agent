"""Decide what to do with a no-tool-call reply from the model.

A reply without tool calls is a candidate conclusion. ``ConclusionHandler``
runs the acceptance protocol the ReAct loop (``core.agent.react_loop``) used
to inline: bounce a tool invocation degraded into plain text, ask the host
whether the conclusion may stand, replay a queued follow-up, or push the
host's nudge back into the conversation. It owns the per-run textual-nudge
counter; the loop only records the outcome (final text, cap flag).
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent.loop_host import LoopHost
from core.agent.textual_tool_call import TEXTUAL_TOOL_CALL_NUDGE, looks_like_textual_tool_call
from core.events import TurnEndEvent
from core.messages import RuntimeMessage, UserRuntimeMessage
from core.types import RuntimeTool

logger = logging.getLogger(__name__)

# How many times per run a no-tool-call reply that is really a tool invocation
# written as plain text gets bounced back to the model. Bounded so a model that
# keeps degrading its tool calls cannot burn the whole iteration budget on
# nudges; after the cap the reply is handled as an ordinary conclusion.
_MAX_TEXTUAL_TOOL_CALL_NUDGES = 2


class ConclusionHandler[RuntimeToolT: RuntimeTool]:
    """Accept, follow up on, or nudge a reply that requested no tools.

    Shares the loop's live message list and host by reference; ``handle``
    appends nudges and follow-ups in place, exactly as the loop did when this
    logic lived inline.
    """

    def __init__(
        self,
        host: LoopHost[RuntimeToolT],
        messages: list[RuntimeMessage],
        runtime_tools: list[RuntimeToolT],
    ) -> None:
        self._host = host
        self._messages = messages
        self._runtime_tools = runtime_tools
        self._textual_tool_call_nudges = 0

    def handle(
        self,
        response: Any,
        assistant_message: Any,
        iteration: int,
        *,
        evidence_count: int,
    ) -> bool:
        """Return True when the conclusion is accepted and the loop should stop."""
        # Transport robustness, like the context budget: a reply whose whole
        # body is a tool invocation written as plain text is a degraded tool
        # call, not a conclusion — accepting it would end the turn with the
        # action never executed. Bounce it back before asking the host.
        if self._textual_tool_call_nudges < _MAX_TEXTUAL_TOOL_CALL_NUDGES and (
            looks_like_textual_tool_call(response.content or "", self._runtime_tools)
        ):
            self._textual_tool_call_nudges += 1
            logger.debug(
                "[runtime] textual tool call in reply body at iteration %s; nudging", iteration
            )
            self._messages.append(UserRuntimeMessage(content=TEXTUAL_TOOL_CALL_NUDGE))
            self._host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": False, "textual_tool_call_nudge": True},
                )
            )
            return False
        accept, nudge = self._host._should_accept_conclusion(
            evidence_count=evidence_count,
            iteration=iteration,
            final_text=response.content or "",
        )
        if accept:
            follow_up = self._host._pop_follow_up_message()
            if follow_up is not None:
                self._messages.append(UserRuntimeMessage(content=follow_up))
                self._host._emit_runtime(
                    TurnEndEvent(
                        iteration=iteration,
                        message=assistant_message,
                        data={"accepted": False, "queued_follow_up": True},
                    )
                )
                return False
            self._host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": True},
                )
            )
            return True
        if nudge is None:
            raise ValueError(
                f"{type(self._host).__name__}._should_accept_conclusion returned "
                "(False, None) — a nudge string is required when rejecting "
                "the conclusion, otherwise the LLM will loop on an unchanged "
                "message history until max_iterations."
            )
        self._messages.append(UserRuntimeMessage(content=nudge))
        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                data={"accepted": False, "nudge": True},
            )
        )
        return False


__all__ = ["ConclusionHandler"]
