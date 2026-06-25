from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from tools.investigation_registry import prioritization
from tools.registered_tool import RegisteredTool


def _tool(name: str, source: str, use_cases: list[str] | None = None) -> RegisteredTool:
    def _run(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    return RegisteredTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        source=source,  # type: ignore[arg-type]
        run=cast(Callable[..., Any], _run),
        use_cases=use_cases or [],
    )


def test_prioritization_scores_source_and_keywords(monkeypatch: Any) -> None:
    actions = [
        _tool("query_datadog_logs", "datadog", ["inspect error logs"]),
        _tool("query_github_commits", "github", ["inspect commits"]),
    ]
    monkeypatch.setattr(prioritization, "get_available_actions", lambda: actions)

    prioritized, reasons = prioritization.get_prioritized_actions_with_reasons(
        sources=["datadog"],
        keywords=["error"],
    )

    assert [action.name for action in prioritized] == [
        "query_datadog_logs",
        "query_github_commits",
    ]
    assert reasons[0]["score"] > reasons[1]["score"]


def test_prioritization_marks_deterministic_fallback(monkeypatch: Any) -> None:
    actions = [
        _tool("query_github_commits", "github"),
        _tool("get_sre_guidance", "knowledge"),
    ]
    monkeypatch.setattr(prioritization, "get_available_actions", lambda: actions)

    _prioritized, reasons = prioritization.get_prioritized_actions_with_reasons(
        sources=["datadog"],
        keywords=["postgres"],
    )

    guidance = next(item for item in reasons if item["name"] == "get_sre_guidance")
    assert prioritization.DETERMINISTIC_FALLBACK_REASON in guidance["reasons"]
