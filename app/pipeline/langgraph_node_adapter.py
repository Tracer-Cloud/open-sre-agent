"""Bridge LangGraph RunnableConfig injection into NodeConfig-typed nodes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional, cast

from langchain_core.runnables.base import RunnableConfig

from app.state import AgentState
from app.types.config import NodeConfig

NodeWithConfig = Callable[[AgentState, NodeConfig | None], dict[str, Any]]

__all__ = ("NodeWithConfig", "_accept_langgraph_config")


def _accept_langgraph_config(func: NodeWithConfig) -> Callable[..., dict[str, Any]]:
    """Adapt a NodeConfig-typed node for LangGraph runtime injection."""

    def _wrapped(
        state: AgentState,
        config: Optional[RunnableConfig] = None,  # noqa: UP045 -- LangGraph matches Optional[], not |
    ) -> dict[str, Any]:
        return func(state, cast(NodeConfig | None, config))

    _wrapped.__name__ = func.__name__
    _wrapped.__qualname__ = func.__qualname__
    return _wrapped
