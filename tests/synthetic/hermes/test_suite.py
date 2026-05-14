"""Synthetic suite: run the Hermes classifier over each fixture log.

The suite is intentionally offline. It loads each scenario's
``errors.log``, parses every line into :class:`LogRecord` objects via
:func:`app.hermes.parser.parse_log_line`, and feeds them to a fresh
:class:`IncidentClassifier` configured by the scenario's YAML. The
emitted :class:`HermesIncident` list is then validated against
``answer.yml``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.hermes.classifier import IncidentClassifier
from app.hermes.incident import HermesIncident, LogLevel
from app.hermes.parser import parse_log_line
from tests.synthetic.hermes.scenario_loader import (
    ExpectedIncident,
    HermesScenarioFixture,
    load_all_scenarios,
)

pytestmark = pytest.mark.synthetic

_SCENARIOS = load_all_scenarios()


def _classify(fixture: HermesScenarioFixture) -> list[HermesIncident]:
    classifier = IncidentClassifier(
        warning_burst_threshold=fixture.metadata.classifier.warning_burst_threshold,
        warning_burst_window_s=fixture.metadata.classifier.warning_burst_window_s,
        traceback_followup_s=fixture.metadata.classifier.traceback_followup_s,
    )
    incidents: list[HermesIncident] = []
    prev_level: LogLevel | None = None
    for line in fixture.log_lines:
        record = parse_log_line(line, prev_level=prev_level)
        if record is None:
            continue
        if not record.is_continuation:
            prev_level = record.level
        incidents.extend(classifier.observe(record))
    incidents.extend(classifier.flush())
    return incidents


def _matches(expected: ExpectedIncident, incident: HermesIncident) -> bool:
    if incident.rule != expected.rule:
        return False
    if expected.severity is not None and incident.severity.value != expected.severity:
        return False
    if expected.logger is not None and incident.logger != expected.logger:
        return False
    if expected.title_contains is not None and expected.title_contains not in incident.title:
        return False
    if len(incident.records) < expected.min_records:
        return False
    return not (expected.run_id is not None and incident.run_id != expected.run_id)


def _assert_ordered_partial_match(
    expected: tuple[ExpectedIncident, ...],
    incidents: list[HermesIncident],
) -> None:
    """Each expected entry must match a later incident than the previous one.

    This is intentionally tolerant: the suite asserts the *presence and
    order* of expected incidents, not that the emitted stream contains
    only those incidents. Use ``expected_incident_count`` for exhaustive
    cardinality checks.
    """
    cursor = 0
    for entry_index, entry in enumerate(expected):
        match_index: int | None = None
        for offset, incident in enumerate(incidents[cursor:]):
            if _matches(entry, incident):
                match_index = cursor + offset
                break
        if match_index is None:
            pytest.fail(
                f"expected incident #{entry_index} {entry!r} not found in "
                f"emitted stream {[(i.rule, i.logger, len(i.records)) for i in incidents]}"
            )
        cursor = match_index + 1


@pytest.mark.parametrize(
    "fixture",
    _SCENARIOS,
    ids=[fixture.scenario_id for fixture in _SCENARIOS],
)
def test_synthetic_hermes_scenario(fixture: HermesScenarioFixture) -> None:
    incidents = _classify(fixture)

    _assert_ordered_partial_match(fixture.answer_key.expected_incidents, incidents)

    counts = Counter(incident.rule for incident in incidents)
    for rule, assertion in fixture.answer_key.expected_incident_count.items():
        observed = counts.get(rule, 0)
        assert assertion.matches(observed), (
            f"{fixture.scenario_id}: incident count for rule {rule!r} = "
            f"{observed}, expected {assertion.op}{assertion.value}"
        )


def test_suite_discovered_at_least_one_scenario() -> None:
    # Guard against an empty discovery (e.g. someone moves the directory
    # and the parametrize above silently degenerates to zero cases).
    assert _SCENARIOS, "no Hermes synthetic scenarios discovered"
