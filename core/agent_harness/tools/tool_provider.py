"""Core-owned default tool provider for the shared agent harness."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.agent_harness.ports import (
    ConfirmFn,
    ToolEventObserver,
)
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_context
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolContext,
)

ActionObserverFactory = Callable[[str], ToolEventObserver]
# Return value is tools.interactive_shell.subprocess.SubprocessPresenter (surface-injected).
SubprocessPresenterFactory = Callable[
    [Any, Any, ConfirmFn | None, bool | None, bool],
    Any,
]
_TOOL_INPUT_LOG_PREVIEW_LIMIT = 500


def _tool_input_preview(value: Any) -> str:
    preview = repr(value)
    if len(preview) > _TOOL_INPUT_LOG_PREVIEW_LIMIT:
        return f"{preview[: _TOOL_INPUT_LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class DefaultToolProvider:
    """:class:`core.agent_harness.ports.ToolProvider` backed by action tools."""

    def __init__(
        self,
        session: Any,
        console: Any,
        *,
        request_exit: Callable[[], None] | None = None,
        precomputed_action_tools: list[Any] | None = None,
        observer_factory: ActionObserverFactory | None = None,
        tool_action_logger: logging.Logger | None = None,
        subprocess_presenter_factory: SubprocessPresenterFactory | None = None,
    ) -> None:
        self._session = session
        self._console = console
        self._request_exit = request_exit
        self._precomputed_action_tools = precomputed_action_tools
        self._observer_factory = observer_factory
        self._tool_action_logger = tool_action_logger
        self._subprocess_presenter_factory = subprocess_presenter_factory
        self._tool_context: ActionToolContext | None = None

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[Any]:
        subprocess_presenter = None
        if self._subprocess_presenter_factory is not None:
            subprocess_presenter = self._subprocess_presenter_factory(
                self._session,
                self._console,
                confirm_fn,
                is_tty,
                True,
            )
        ctx = ActionToolContext(
            session=self._session,
            console=self._console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=self._request_exit,
            action_already_listed=True,
            subprocess_presenter=subprocess_presenter,
        )
        self._tool_context = ctx
        if self._precomputed_action_tools is not None:
            return list(self._precomputed_action_tools)
        resolved = (
            resolved_integrations
            if resolved_integrations is not None
            else self._resolved_integrations()
        )
        return get_action_tools_from_integrations_context(ctx, resolved_integrations=resolved)

    def tool_resources(self) -> dict[str, Any]:
        if self._tool_context is None:
            return {}
        return {ACTION_TOOL_CONTEXT_RESOURCE_KEY: self._tool_context}

    def observer(self, *, message: str) -> ToolEventObserver:
        if self._observer_factory is not None:
            observer = self._observer_factory(message)
        else:

            def observer(_kind: str, _data: dict[str, Any]) -> None:
                return None

        if self._tool_action_logger is None:
            return observer
        logger = self._tool_action_logger

        def _logging_observer(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_start":
                tool_name = str(data.get("name") or "").strip()
                if tool_name:
                    logger.info(
                        "tool action name=%s input=%s",
                        tool_name,
                        _tool_input_preview(data.get("input", {})),
                    )
            elif kind == "tool_end":
                tool_name = str(data.get("name") or "").strip()
                if tool_name:
                    from core.events import tool_result_is_error

                    output = data.get("output")
                    logger.info(
                        "tool result name=%s ok=%s size=%d",
                        tool_name,
                        not tool_result_is_error(output),
                        len(str(output)),
                    )
            observer(kind, data)

        return _logging_observer

    def _resolved_integrations(self) -> dict[str, Any]:
        from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations

        # resolve_and_cache_integrations returns a fresh dict.
        return resolve_and_cache_integrations(self._session)
