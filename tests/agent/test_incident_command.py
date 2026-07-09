from __future__ import annotations

from tools.investigation.stages.gather_evidence.incident_command import (
    incident_command_conclusion_complete,
)


def test_incident_command_conclusion_complete_requires_all_markers() -> None:
    complete = """
    Triage complete: payments_etl only, critical since 14:32 UTC.
    Status — confirmed: alert is critical | open: deploy time | next: verify DB | owner: on-call
    [MISSING CONTEXT: recent deploy time/SHA for payments_etl]
    Remediation trade-offs: rollback is fastest; scaling DB is slower but safer. Recommend rollback first.
    Root cause: connection failures to orders-db.
    """
    assert incident_command_conclusion_complete(complete) is True


def test_incident_command_conclusion_complete_accepts_explicit_none_missing_context() -> None:
    complete = """
    Triage complete: isolated to payments_etl.
    Status — confirmed: DB errors in alert | open: root cause | next: check DB logs | owner: platform
    [MISSING CONTEXT: none — alert provides sufficient scope]
    Remediation trade-offs: N/A — single clear fix path
    """
    assert incident_command_conclusion_complete(complete) is True


def test_incident_command_conclusion_complete_rejects_partial_text() -> None:
    partial = "Root cause: database connection failure."
    assert incident_command_conclusion_complete(partial) is False
