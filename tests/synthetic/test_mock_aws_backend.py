"""Tests for FixtureAWSBackend — the synthetic AWS shim.

These pin the contract that lets RDS investigation tools serve fixture data
during synthetic scenario runs without ever reaching real boto3. The
contract has two halves:

1. ``describe_db_instances`` is foundational metadata. Every scenario carries
   the DB identity in ``scenario.yml``, so the call MUST always succeed for
   the scenario's own identifier. A foreign identifier mirrors the real
   boto3 ``DBInstanceNotFound`` envelope so synthetic runs are honest.
2. ``describe_db_events`` is a data feed gated on ``aws_rds_events`` evidence
   being declared in the scenario. Calling without that opt-in must raise
   loudly; silently returning "no events" would let scenarios accidentally
   teach the agent that absence of events is evidence.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests.synthetic.mock_aws_backend import FixtureAWSBackend
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    ScenarioFixture,
    load_scenario,
)


def _load(scenario_name: str) -> ScenarioFixture:
    return load_scenario(SUITE_DIR / scenario_name)


def _without_evidence(fixture: ScenarioFixture, **fields_to_clear: object) -> ScenarioFixture:
    """Return a copy of *fixture* with the named evidence fields set to None.

    Avoids the trap of "every shipped scenario declares aws_rds_events" — we
    can't pick a real scenario that omits it, so synthesize one in-memory.
    """
    cleared_evidence = dataclasses.replace(fixture.evidence, **fields_to_clear)
    return dataclasses.replace(fixture, evidence=cleared_evidence)


def test_describe_db_instances_synthesizes_response_from_scenario_metadata() -> None:
    fixture = _load("015-mysql-ec2-load-attribution")
    backend = FixtureAWSBackend(fixture)

    response = backend.describe_db_instances(
        db_instance_identifier="orders-prod",
        region="us-east-1",
    )

    assert response["available"] is True
    assert response["db_instance_identifier"] == "orders-prod"
    assert response["engine"] == "mysql"
    assert response["engine_version"] == "8.0"
    assert response["instance_class"] == "db.r6g.xlarge"
    assert response["status"] == "available"
    assert response["endpoint"]["port"] == 3306
    assert "orders-prod" in response["endpoint"]["address"]
    assert response["error"] is None


def test_describe_db_instances_picks_postgres_port_for_postgres_engine() -> None:
    fixture = _load("001-replication-lag")
    backend = FixtureAWSBackend(fixture)

    response = backend.describe_db_instances(
        db_instance_identifier=fixture.metadata.db_instance_identifier,
    )

    assert response["available"] is True
    assert response["engine"].startswith("postgres")
    assert response["endpoint"]["port"] == 5432


def test_describe_db_instances_returns_not_found_envelope_for_foreign_identifier() -> None:
    """Mirror the real boto3 ``DBInstanceNotFound`` semantics so synthetic
    scenarios stay honest: if the agent asks about a DB the scenario didn't
    define, we report it as missing rather than fabricating a successful
    response.
    """
    fixture = _load("015-mysql-ec2-load-attribution")
    backend = FixtureAWSBackend(fixture)

    response = backend.describe_db_instances(db_instance_identifier="some-other-db")

    assert response["available"] is False
    assert "No RDS instance" in response["error"]
    assert response["db_instance_identifier"] == "some-other-db"


def test_describe_db_events_serves_aws_rds_events_fixture() -> None:
    fixture = _load("015-mysql-ec2-load-attribution")
    backend = FixtureAWSBackend(fixture)

    response = backend.describe_db_events(
        db_instance_identifier="orders-prod",
        duration_minutes=120,
    )

    assert response["available"] is True
    assert response["duration_minutes"] == 120
    assert response["total_events"] == 1
    event = response["events"][0]
    assert "Automated backup" in event["message"]
    assert event["source_type"] == "db-instance"
    assert event["categories"] == ["backup"]


def test_describe_db_events_raises_when_evidence_not_declared() -> None:
    """Without ``aws_rds_events`` in ``available_evidence`` we must raise.

    Returning an empty list would silently teach the agent that absence of
    events is evidence — exactly the kind of false-negative we want the
    synthetic suite to catch loudly during scenario authoring.
    """
    fixture = _without_evidence(_load("000-healthy"), aws_rds_events=None)
    backend = FixtureAWSBackend(fixture)

    with pytest.raises(ValueError, match="aws_rds_events"):
        backend.describe_db_events(db_instance_identifier=fixture.metadata.db_instance_identifier)


def test_describe_db_events_returns_empty_list_for_foreign_identifier() -> None:
    """A foreign identifier matches no events; this mirrors how real RDS
    ``describe_events`` behaves for unknown source identifiers."""
    fixture = _load("015-mysql-ec2-load-attribution")
    backend = FixtureAWSBackend(fixture)

    response = backend.describe_db_events(db_instance_identifier="not-this-db")

    assert response["available"] is True
    assert response["total_events"] == 0
    assert response["events"] == []
