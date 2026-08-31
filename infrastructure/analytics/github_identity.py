"""GitHub identity for analytics events."""

from __future__ import annotations

from infrastructure.analytics.provider import get_analytics
from infrastructure.observability.errors.sentry import capture_exception


def identify_saved_github_username() -> None:
    """Re-attach a previously saved GitHub handle to PostHog for this process.

    The integration store persists ``credentials.username`` across REPL sessions
    (used by the welcome banner), but analytics persistent properties are
    in-memory per CLI process. Call at REPL boot so events like
    ``$ai_generation`` include ``github_username`` without requiring a fresh
    device-flow login each session.
    """
    from integrations.github import saved_github_username

    identify_github_username(saved_github_username())


def identify_github_username(username: str) -> None:
    """Attach the authenticated GitHub username to PostHog.

    Calls :meth:`~infrastructure.analytics.provider.Analytics.identify` to persist
    ``github_username`` on the person profile AND
    :meth:`~infrastructure.analytics.provider.Analytics.set_persistent_property` so the
    property is stamped directly on every subsequent event.  Both are needed:
    the ``$identify`` call keeps the person profile up-to-date for cohort
    queries, while the persistent property makes ``github_username`` queryable
    as a plain ``properties.github_username`` filter on any event without
    requiring a person-profile join.

    No-op for an empty username. Best-effort: telemetry kill-switches make the
    underlying calls no-ops, and any unexpected error is swallowed to Sentry.
    """
    if not username:
        return
    try:
        analytics = get_analytics()
        analytics.identify({"github_username": username})
        analytics.set_persistent_property("github_username", username)
    except Exception as exc:
        capture_exception(exc)
