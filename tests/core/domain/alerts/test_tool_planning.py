from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.domain.alerts.tool_planning import (
    metadata_matches_for_alert,
    score_tools,
)


@dataclass(frozen=True)
class _Tool:
    name: str
    source: str
    description: str = ""
    use_cases: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    evidence_type: Any | None = None


def test_score_tools_prioritizes_primary_alert_source() -> None:
    scored = score_tools(
        {"alert_source": "datadog"},
        [
            _Tool("query_github_commits", "github"),
            _Tool("query_datadog_logs", "datadog"),
        ],
    )

    assert scored[0].name == "query_datadog_logs"
    assert scored[0].score == 100
    assert "matches alert source" in " ".join(scored[0].reasons)


def test_score_tools_uses_context_sources_for_generic_alert() -> None:
    scored = score_tools(
        {
            "raw_alert": {
                "commonAnnotations": {
                    "context_sources": "github",
                }
            }
        },
        [
            _Tool("query_datadog_logs", "datadog"),
            _Tool("query_github_commits", "github"),
        ],
    )

    assert scored[0].name == "query_github_commits"
    assert scored[0].score == 70


def test_score_tools_uses_guidance_fallback_when_nothing_matches() -> None:
    scored = score_tools(
        {"alert_source": "generic", "message": "mysterious failure"},
        [
            _Tool("query_github_commits", "github"),
            _Tool("get_sre_guidance", "knowledge"),
        ],
    )

    assert scored[0].name == "get_sre_guidance"
    assert "fallback" in " ".join(scored[0].reasons)


def test_metadata_matches_for_alert_filters_short_terms() -> None:
    matches = metadata_matches_for_alert(
        "api db latency timeout",
        "investigate timeout and latency for service APIs",
    )

    assert matches == ["latency", "timeout"]
