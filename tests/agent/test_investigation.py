from __future__ import annotations

from app.agent.investigation import _availability_view


def test_availability_view_marks_configured_integrations_without_mutating_state() -> None:
    resolved = {"github": {"access_token": "token"}, "_all": [{"service": "github"}]}

    view = _availability_view(resolved)

    assert view["github"]["connection_verified"] is True
    assert "connection_verified" not in resolved["github"]
    assert view["_all"] == resolved["_all"]
