"""Read-only discovery commands stash a compact observation for the agent.

This is what lets the observe→answer loop summarize "is sentry installed?" from
the actual ``/integrations`` output instead of leaving the user with a raw table.
"""

from __future__ import annotations

from interactive_shell.command_registry.integrations import (
    _MAX_OBSERVATION_DETAIL_CHARS,
    _record_integration_show_observation,
    _record_integrations_observation,
)
from interactive_shell.session import ReplSession


def test_records_status_lines_for_each_service() -> None:
    session = ReplSession()
    results = [
        {"service": "datadog", "source": "local store", "status": "passed", "detail": "Connected."},
        {"service": "sentry", "source": "-", "status": "missing", "detail": "Not configured."},
    ]

    _record_integrations_observation(session, results)

    assert session.agent.last_observation is not None
    obs = session.agent.last_observation
    assert "datadog: passed" in obs
    assert "sentry: missing" in obs
    assert "Not configured." in obs


def test_truncates_long_detail() -> None:
    session = ReplSession()
    long_detail = "x" * (_MAX_OBSERVATION_DETAIL_CHARS + 50)
    _record_integrations_observation(
        session, [{"service": "datadog", "status": "passed", "detail": long_detail}]
    )

    assert session.agent.last_observation is not None
    # Detail is bounded (ellipsis substituted for the tail) so prompts stay cheap.
    assert "…" in session.agent.last_observation
    assert long_detail not in session.agent.last_observation


def test_skips_rows_without_a_service_name() -> None:
    session = ReplSession()
    _record_integrations_observation(session, [{"service": "", "status": "missing"}])
    assert session.agent.last_observation is None


def test_show_observation_renders_key_values() -> None:
    session = ReplSession()
    _record_integration_show_observation(
        session, {"service": "datadog", "status": "passed", "monitors": "14"}
    )
    assert session.agent.last_observation is not None
    assert "service: datadog" in session.agent.last_observation
    assert "monitors: 14" in session.agent.last_observation
