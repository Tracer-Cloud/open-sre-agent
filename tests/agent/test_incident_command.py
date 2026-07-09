from __future__ import annotations

from tools.investigation.stages.gather_evidence.incident_command import (
    incident_command_conclusion_complete,
)


def test_incident_command_conclusion_complete_requires_all_markers() -> None:
    complete = """
    Triage complete: payments_etl only, critical since 14:32 UTC.
    Status — confirmed: alert is critical | open: deploy time | next: verify DB | owner: on-call
    Hypotheses:
    1. Database dependency outage — confirm: DB error logs; rule out: caller-only misconfig
    2. Bad deploy/config — confirm: deploy at incident start; rule out: no recent deploy
    Verification:
    1. Datadog logs (H1): connection refused errors in payments_etl
    2. Grafana Loki (H1): no DB-side logs available
    Follow-up questions:
    1. Was there a deploy of payments_etl around 14:32 UTC?
    2. Are downstream jobs or users also failing?
    Remediation trade-offs: rollback is fastest; scaling DB is slower but safer.
    Root cause: connection failures to orders-db.
    """
    assert incident_command_conclusion_complete(complete) is True


def test_incident_command_conclusion_complete_accepts_explicit_none_follow_ups() -> None:
    complete = """
    Triage complete: isolated to payments_etl.
    Status — confirmed: DB errors in alert | open: root cause | next: check DB logs | owner: platform
    Hypotheses:
    1. Misconfigured DB endpoint — confirm: wrong host in config; rule out: endpoint matches prod
    Verification:
    1. Alert text (H1): repeated database connection errors reported
    Follow-up questions: none — alert provides sufficient scope
    Remediation trade-offs: N/A — single clear fix path
    """
    assert incident_command_conclusion_complete(complete) is True


def test_incident_command_conclusion_complete_rejects_partial_text() -> None:
    partial = "Root cause: database connection failure."
    assert incident_command_conclusion_complete(partial) is False
