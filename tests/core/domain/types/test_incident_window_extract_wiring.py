"""Regression tests for the extract_alert -> incident_window wiring.

The first test pins the P1 Greptile finding: passing the post-enrichment
``enriched_alert`` to ``resolve_incident_window`` silently lost timestamps
for any string-form webhook payload, defeating the whole feature. This
test confirms a string raw_alert containing ``startsAt`` is still
correctly anchored after the fix.

The second test confirms Grafana-shaped payloads still resolve to
``alert.startsAt`` after the dead ``_grafana_anchor`` shim was removed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from core.domain.types.incident_window import (
    SOURCE_DEFAULT,
    SOURCE_STARTS_AT,
    resolve_incident_window,
)
from surfaces.cli.investigation.payload import load_file
from tools.investigation.state_factory import make_initial_state

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


def test_string_payload_resolves_via_coerce_dict() -> None:
    """A JSON-string raw_alert must be parsed and anchored correctly.

    extract_alert now passes the original raw_alert (not the LLM-enriched
    dict that discards string content). resolve_incident_window must be
    able to coerce the JSON string into a dict and find the timestamp.
    """
    payload_str = json.dumps(
        {
            "status": "firing",
            "alerts": [{"startsAt": "2026-04-20T09:00:00Z"}],
        }
    )
    result = resolve_incident_window(payload_str, now=NOW)
    assert result.source == SOURCE_STARTS_AT
    # 09:00 + 10min default buffer = 09:10
    assert result.until == datetime(2026, 4, 20, 9, 10, tzinfo=UTC)


def test_string_payload_with_no_timestamp_falls_back_to_default() -> None:
    """The fallback path is what the bug used to hit for every string
    payload. Confirm it still works when there is genuinely no anchor."""
    payload_str = json.dumps({"alertname": "noisy", "severity": "info"})
    result = resolve_incident_window(payload_str, now=NOW)
    assert result.source == SOURCE_DEFAULT


def test_k8s_fixture_envelope_anchors_on_nested_starts_at() -> None:
    """CLI ``-i datadog_k8s_alert.json`` loads the whole fixture envelope.

    Regression for #3813: without unwrapping ``alert``, the resolver fell back
    to the default wall-clock window instead of ``alert.startsAt``.
    """
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "e2e"
        / "kubernetes"
        / "fixtures"
        / "datadog_k8s_alert.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = resolve_incident_window(fixture, now=NOW)
    assert result.source == SOURCE_STARTS_AT
    assert result.until == datetime(2026, 2, 19, 0, 10, tzinfo=UTC)


def test_cli_load_path_anchors_k8s_fixture_envelope() -> None:
    """``load_file`` + ``make_initial_state`` must still anchor the window."""
    fixture_path = "tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json"
    state = make_initial_state(raw_alert=load_file(fixture_path))
    result = resolve_incident_window(state["raw_alert"], now=NOW)
    assert result.source == SOURCE_STARTS_AT
    # Pin the exact anchor so a silent timestamp drift in the CLI load path
    # (e.g. timezone coercion) fails loudly instead of passing on source alone.
    assert result.until == datetime(2026, 2, 19, 0, 10, tzinfo=UTC)


def test_grafana_payload_still_resolves_after_parser_removal() -> None:
    """Grafana managed alerts share Alertmanager's schema. The dedicated
    _grafana_anchor was a dead delegate to _alertmanager_anchor; removing
    it must not regress Grafana coverage. Today the Alertmanager parser
    handles both shapes."""
    payload = {
        "receiver": "grafana-default",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"grafana_folder": "Prod"},
                "startsAt": "2026-04-20T10:15:00Z",
            }
        ],
        "externalURL": "https://grafana.example.com",
    }
    result = resolve_incident_window(payload, now=NOW)
    assert result.source == SOURCE_STARTS_AT
    assert result.until == datetime(2026, 4, 20, 10, 25, tzinfo=UTC)
