from __future__ import annotations

from typing import Any

import pytest

from tools.base import BaseTool
from tools.registered_tool import REGISTERED_TOOL_ATTR
from tools.tool_decorator import tool
from platform.observability import sentry_sdk as sentry_mod


class ExplodingBaseTool(BaseTool):
    name = "exploding_base_tool"
    description = "Tool that raises for Sentry coverage."
    input_schema = {"type": "object", "properties": {}}
    source = "grafana"

    def run(self) -> dict[str, Any]:
        raise RuntimeError("base boom")


def test_base_tool_exception_is_captured_with_tool_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []

    def capture_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(sentry_mod, "capture_exception", capture_stub)

    result = ExplodingBaseTool()()

    assert result == {"error": "base boom", "exception_type": "RuntimeError"}
    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert isinstance(exc, RuntimeError)
    assert kwargs["context"] == "tool.exploding_base_tool"
    assert kwargs["tags"] == {"surface": "tool", "tool": "exploding_base_tool"}


def test_decorated_function_tool_exception_is_captured_with_tool_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []

    def capture_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(sentry_mod, "capture_exception", capture_stub)

    @tool(
        name="decorated_failure",
        description="Function tool that raises for Sentry coverage.",
        input_schema={"type": "object", "properties": {}},
        source="grafana",
    )
    def decorated_failure() -> dict[str, Any]:
        raise ValueError("decorated boom")

    registered = getattr(decorated_failure, REGISTERED_TOOL_ATTR)
    result = registered()

    assert result == {"error": "decorated boom", "exception_type": "ValueError"}
    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert isinstance(exc, ValueError)
    assert kwargs["context"] == "tool.decorated_failure"
    assert kwargs["tags"] == {"surface": "tool", "tool": "decorated_failure"}
