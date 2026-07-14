"""Verify scoped project slug is written to session integration cache."""

from __future__ import annotations

from integrations.sentry.morning_digest_runner import _apply_digest_project_scope


class _SessionStub:
    def __init__(self) -> None:
        self.resolved_integrations_cache: dict[str, object] | None = None


def test_apply_digest_project_scope_updates_session_cache(monkeypatch) -> None:
    session = _SessionStub()
    resolved = {
        "sentry": {
            "connection_verified": True,
            "organization_slug": "tracer",
            "auth_token": "token",
        }
    }
    monkeypatch.setattr(
        "integrations.sentry.morning_digest_runner.resolve_and_cache_integrations",
        lambda _session: resolved,
    )

    _apply_digest_project_scope(session, {"project_slug": "checkout-api"})

    assert session.resolved_integrations_cache is not None
    sentry = session.resolved_integrations_cache["sentry"]
    assert isinstance(sentry, dict)
    assert sentry["project_slug"] == "checkout-api"
