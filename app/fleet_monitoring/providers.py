"""Resolve an ``AgentRecord`` to its canonical token-meter provider id."""

from __future__ import annotations

from app.fleet_monitoring.discovery import classify_command_provider
from app.fleet_monitoring.provider_ids import KNOWN_PROVIDERS, provider_from_classified_name
from app.fleet_monitoring.registry import AgentRecord


def provider_for(record: AgentRecord) -> str | None:
    """Return the canonical provider id for ``record``, or ``None`` if unknown.

    Resolution order: persisted ``record.provider`` first, then the
    discovery-style name (``<provider>-<pid>``) stripped of its PID
    suffix, then a backfill via the shared strict command classifier
    in :mod:`app.fleet_monitoring.discovery` (covers legacy ``agents.jsonl``
    rows from before the ``provider`` field existed). ``None`` lets
    the dashboard render ``-``.
    """
    if record.provider is not None:
        return record.provider
    from_name = provider_from_classified_name(record.name)
    if from_name is not None:
        return from_name
    return classify_command_provider(record.command)


def provider_from_command(command: str) -> str | None:
    """Legacy alias for :func:`app.fleet_monitoring.discovery.classify_command_provider`.

    Kept for tests and external callers; the classification engine
    lives in ``discovery`` so register-time wiring and the on-read
    backfill share one implementation.
    """
    return classify_command_provider(command)


__all__ = [
    "KNOWN_PROVIDERS",
    "provider_for",
    "provider_from_classified_name",
    "provider_from_command",
]
