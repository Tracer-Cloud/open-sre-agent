from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.agent_harness import evidence_agent


def test_resolve_session_integrations_ignores_gateway_only_cache() -> None:
    session = MagicMock()
    session.resolved_integrations_cache = {"_gateway_chat_id": "42"}

    with patch(
        "tools.investigation.stages.resolve_integrations.resolve_integrations",
        return_value={"resolved_integrations": {"github": {"token": "x"}}},
    ) as mock_resolve:
        resolved = evidence_agent._resolve_session_integrations(session)

    mock_resolve.assert_called_once()
    assert resolved["github"]["token"] == "x"
    assert resolved["_gateway_chat_id"] == "42"


def test_resolve_session_integrations_preserves_empty_cache() -> None:
    session = MagicMock()
    session.resolved_integrations_cache = {}

    with patch(
        "tools.investigation.stages.resolve_integrations.resolve_integrations",
        return_value={"resolved_integrations": {"github": {"token": "x"}}},
    ) as mock_resolve:
        resolved = evidence_agent._resolve_session_integrations(session)

    mock_resolve.assert_not_called()
    assert resolved == {}
