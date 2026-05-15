from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent.prompt import _build_runbook_section, format_alert_context


def test_build_runbook_section_returns_empty_when_no_match() -> None:
    assert _build_runbook_section(None) == ""
    assert _build_runbook_section({}) == ""


def test_build_runbook_section_includes_slug_and_body() -> None:
    section = _build_runbook_section({"slug": "payments-oom", "body": "Bump heap to 2G."})

    assert "payments-oom" in section
    assert "Bump heap to 2G." in section
    assert "payments-oom" in section


def test_build_runbook_section_returns_empty_when_slug_missing() -> None:
    assert _build_runbook_section({"body": "some body"}) == ""
    assert _build_runbook_section({"slug": "", "body": "some body"}) == ""


def test_build_runbook_section_truncates_at_newline_boundary() -> None:
    body = ("line\n" * 500)[:2100]  # contains newlines, exceeds 2000
    section = _build_runbook_section({"slug": "s", "body": body})

    assert "…(truncated)" in section
    # must not split mid-line — text before truncation marker ends at \n
    marker_idx = section.index("…(truncated)")
    assert section[marker_idx - 1] == "\n"


def test_build_runbook_section_truncates_long_body_no_newline() -> None:
    big = "x" * 5000
    section = _build_runbook_section({"slug": "s", "body": big})

    assert "…(truncated)" in section
    assert "x" * 2001 not in section


def test_format_alert_context_injects_matched_runbook() -> None:
    state = {
        "alert_name": "PaymentsOOM",
        "pipeline_name": "payments-api",
        "severity": "critical",
        "resolved_integrations": {},
        "matched_runbook": {"slug": "payments-oom", "body": "Page on-call."},
    }

    mock_tool = MagicMock()
    mock_tool.source = "grafana"
    mock_tool.name = "query_grafana_logs"
    mock_tool.description = "Query logs"
    mock_tool.is_available.return_value = False

    with patch("app.tools.registry.get_registered_tools", return_value=[]):
        result = format_alert_context(state)

    assert "payments-oom" in result
    assert "Page on-call." in result


def test_format_alert_context_omits_section_without_matched_runbook() -> None:
    state = {
        "alert_name": "SomeAlert",
        "pipeline_name": "some-service",
        "severity": "warning",
        "resolved_integrations": {},
    }

    with patch("app.tools.registry.get_registered_tools", return_value=[]):
        result = format_alert_context(state)

    assert "Relevant team runbook" not in result
