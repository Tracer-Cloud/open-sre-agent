"""No-op ``@traceable`` decorator for optional observability hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def traceable(_name: str = "", **_kw: Any) -> Callable[[_F], _F]:
    """Identity decorator — preserves the function unchanged."""

    def decorator(fn: _F) -> _F:
        return fn

    return decorator
