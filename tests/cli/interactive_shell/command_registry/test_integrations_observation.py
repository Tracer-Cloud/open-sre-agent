"""Read-only discovery commands stash a compact observation for the agent.

This is what lets the observe→answer loop summarize "is sentry installed?" from
the actual ``/integrations`` output instead of leaving the user with a raw table.
"""

from __future__ import annotations

from app.cli.interactive_shell.command_registry.integrations import (
    _MAX_OBSERVATION_DETAIL_CHARS,
    _record_integration_show_observation,
    _record_integrations_observation,
)
from app.cli.interactive_shell.runtime.session import ReplSession


def test_records_status_lines_for_each_service() -> None:
    session = ReplSession()
    results = [
        {"service": "datadog", "source": "local store", "status": "passed", "detail": "Connected."},
        {"service": "sentry", "source": "-", "status": "missing", "detail": "Not configured."},
    ]

    _record_integrations_observation(session, results)

    assert session.last_command_observation is not None
    obs = session.last_command_observation
    assert "datadog: passed" in obs
    assert "sentry: missing" in obs
    assert "Not configured." in obs


def test_truncates_long_detail() -> None:
    session = ReplSession()
    long_detail = "x" * (_MAX_OBSERVATION_DETAIL_CHARS + 50)
    _record_integrations_observation(
        session, [{"service": "datadog", "status": "passed", "detail": long_detail}]
    )

    assert session.last_command_observation is not None
    # Detail is bounded (ellipsis substituted for the tail) so prompts stay cheap.
    assert "…" in session.last_command_observation
    assert long_detail not in session.last_command_observation


def test_skips_rows_without_a_service_name() -> None:
    session = ReplSession()
    _record_integrations_observation(session, [{"service": "", "status": "missing"}])
    assert session.last_command_observation is None


def test_show_observation_renders_key_values() -> None:
    session = ReplSession()
    _record_integration_show_observation(
        session, {"service": "datadog", "status": "passed", "monitors": "14"}
    )
    assert session.last_command_observation is not None
    assert "service: datadog" in session.last_command_observation
    assert "monitors: 14" in session.last_command_observation
