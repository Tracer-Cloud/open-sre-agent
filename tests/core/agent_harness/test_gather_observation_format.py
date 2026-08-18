"""Gather observation formatting: newest tools survive head truncation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from core.agent_harness.turns import evidence_driver
from core.agent_harness.turns.gather_observation import (
    GatheredEvidence,
    count_gather_tool_successes,
)
from core.tool_framework.utils.tool_availability import tool_unavailable


def _tc(name: str, **inp: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, input=inp)


def test_format_observation_newest_first() -> None:
    executed = [
        (_tc("read-data-schema", kind="events"), "events:…"),
        (_tc("execute-sql", query="SELECT 1"), "windows|17|69"),
    ]
    text = evidence_driver._format_observation(executed)
    sql_at = text.index("execute-sql")
    schema_at = text.index("read-data-schema")
    assert sql_at < schema_at
    assert "windows|17|69" in text


def test_format_observation_keeps_late_sql_under_char_budget() -> None:
    """Bulky early schema discovery must not push execute-sql off the prompt."""
    huge = "E" * 3_000
    executed = [
        (_tc("list_posthog_tools"), huge),
        (_tc("read-data-schema", kind="events"), huge),
        (_tc("read-data-schema", kind="props"), huge),
        (_tc("read-data-schema", kind="values"), huge),
        (
            _tc("call_posthog_tool", tool_name="execute-sql"),
            "os|unique_users|events\nwindows|17|69",
        ),
    ]
    with patch.object(evidence_driver, "_MAX_OBSERVATION_CHARS", 4_000):
        text = evidence_driver._format_observation(executed)
    assert "execute-sql" in text
    assert "windows|17|69" in text
    assert "…[truncated" in text
    assert text.lstrip().startswith("Tool: call_posthog_tool")


def test_format_observation_keeps_sql_at_default_12k_budget() -> None:
    """Replay the live shape: ~6 bulky discovery blocks + trailing execute-sql.

    Chronological head-truncation at 12k dropped the Windows count; newest-first
    must keep ``windows|17|69`` under the production ``_MAX_OBSERVATION_CHARS``.
    """
    # Per-tool bodies just under the 4k per-tool cap so six of them exceed 12k.
    huge = "E" * 3_500
    executed = [
        (_tc("list_posthog_tools", name_filter="sql"), huge),
        (_tc("list_posthog_tools", name_filter="exec"), huge),
        (_tc("call_posthog_tool", tool_name="read-data-schema"), huge),
        (_tc("call_posthog_tool", tool_name="read-data-schema"), huge),
        (_tc("call_posthog_tool", tool_name="read-data-schema"), huge),
        (
            _tc("call_posthog_tool", tool_name="execute-sql"),
            "os|unique_users|events\nlinux|1251|4855\ndarwin|59|2614\nwindows|17|69",
        ),
    ]
    text = evidence_driver._format_observation(executed)
    assert "execute-sql" in text
    assert "windows|17|69" in text
    assert len(text) <= evidence_driver._MAX_OBSERVATION_CHARS + 80


def test_count_gather_tool_successes_skips_unavailable() -> None:
    assert count_gather_tool_successes(None) == 0
    evidence = GatheredEvidence(
        observation="x",
        tool_results=(
            ("list_posthog_tools", {"tools": []}),
            ("call_posthog_tool", tool_unavailable("posthog", "auth failed")),
            ("call_posthog_tool", "windows|272"),
        ),
    )
    # list_* roster probes do not count; unavailable does not count.
    assert count_gather_tool_successes(evidence) == 1


def test_count_gather_tool_successes_falls_back_to_observation_blocks() -> None:
    """Empty tool_results still counts Tool: blocks in the rendered observation."""
    observation = (
        "Tool: list_posthog_tools\nArguments: {}\nResult: []\n\n"
        "Tool: call_posthog_tool\nArguments: {}\nResult: windows|272"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert count_gather_tool_successes(evidence) == 1


def test_count_gather_tool_successes_observation_fallback_skips_unavailable() -> None:
    """Rendered ``tool_unavailable`` envelopes must not count as gather evidence."""
    from core.tool_framework.utils.tool_availability import tool_unavailable

    observation = (
        "Tool: call_posthog_tool\nArguments: {}\nResult: "
        f"{tool_unavailable('posthog', 'auth failed')}"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert count_gather_tool_successes(evidence) == 0
