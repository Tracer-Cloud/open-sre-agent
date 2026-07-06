from __future__ import annotations

from typing import Any

import pytest

import core.tool_framework.telemetry as telemetry_mod
from core.tool_framework.base import BaseTool
from core.tool_framework.registered_tool import REGISTERED_TOOL_ATTR
from core.tool_framework.tool_decorator import tool


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

    def report_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(telemetry_mod, "report_exception", report_stub)

    result = ExplodingBaseTool()()

    assert result == {"error": "base boom", "exception_type": "RuntimeError"}
    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert isinstance(exc, RuntimeError)
    assert kwargs["tags"] == {
        "surface": "tool",
        "tool_name": "exploding_base_tool",
        "source": "grafana",
    }  # type: ignore[index]


def test_decorated_function_tool_exception_is_captured_with_tool_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []

    def report_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(telemetry_mod, "report_exception", report_stub)

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
    assert kwargs["tags"] == {
        "surface": "tool",
        "tool_name": "decorated_failure",
        "source": "grafana",
    }  # type: ignore[index]
