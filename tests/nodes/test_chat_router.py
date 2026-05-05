"""Tests for chat router_node intent classification (mocked LLM)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.nodes import chat as chat_mod


def _mock_router_response(content: str) -> MagicMock:
    response = MagicMock()
    response.content = content
    return response


@pytest.fixture
def mock_router_llm():
    """Patch get_llm_for_tools so router_node never makes a real LLM call."""
    with patch.object(chat_mod, "get_llm_for_tools") as mock_factory:
        llm = MagicMock()
        mock_factory.return_value = llm
        yield llm


def test_router_returns_general_when_no_messages(mock_router_llm: MagicMock) -> None:
    out = chat_mod.router_node({"messages": []})
    assert out == {"route": "general"}
    mock_router_llm.invoke.assert_not_called()


def test_router_returns_general_when_last_message_is_not_user(
    mock_router_llm: MagicMock,
) -> None:
    out = chat_mod.router_node({"messages": [{"role": "assistant", "content": "hi"}]})
    assert out == {"route": "general"}
    mock_router_llm.invoke.assert_not_called()


def test_router_returns_tracer_data_when_llm_says_so(mock_router_llm: MagicMock) -> None:
    mock_router_llm.invoke.return_value = _mock_router_response("tracer_data")
    out = chat_mod.router_node(
        {"messages": [{"role": "user", "content": "investigate this alert"}]}
    )
    assert out == {"route": "tracer_data"}


def test_router_returns_general_when_llm_says_so(mock_router_llm: MagicMock) -> None:
    mock_router_llm.invoke.return_value = _mock_router_response("general")
    out = chat_mod.router_node({"messages": [{"role": "user", "content": "what is SLO?"}]})
    assert out == {"route": "general"}


def test_router_normalizes_whitespace_and_case(mock_router_llm: MagicMock) -> None:
    mock_router_llm.invoke.return_value = _mock_router_response("  Tracer_Data\n")
    out = chat_mod.router_node({"messages": [{"role": "user", "content": "x"}]})
    assert out == {"route": "tracer_data"}


def test_router_falls_back_to_general_for_unknown_label(mock_router_llm: MagicMock) -> None:
    mock_router_llm.invoke.return_value = _mock_router_response("maybe_tracer")
    out = chat_mod.router_node({"messages": [{"role": "user", "content": "x"}]})
    assert out == {"route": "general"}


def test_router_uses_router_prompt_as_system(mock_router_llm: MagicMock) -> None:
    mock_router_llm.invoke.return_value = _mock_router_response("general")
    chat_mod.router_node({"messages": [{"role": "user", "content": "hello"}]})

    call_args = mock_router_llm.invoke.call_args[0][0]
    assert call_args[0]["role"] == "system"
    assert "tracer_data" in call_args[0]["content"]
    assert "general" in call_args[0]["content"]
    assert call_args[1] == {"role": "user", "content": "hello"}
