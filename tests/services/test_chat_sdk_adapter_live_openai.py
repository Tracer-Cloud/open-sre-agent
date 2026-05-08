"""Optional live OpenAI Chat Completions smoke for ``chat_sdk_adapter``.

Run when ``OPENAI_API_KEY`` is set::
    uv run pytest tests/services/test_chat_sdk_adapter_live_openai.py -v

Override model (default ``gpt-4o-mini``)::
    export CHAT_SDK_LIVE_OPENAI_MODEL=gpt-5.4-mini

Mocked tests stay in ``test_chat_sdk_adapter_comprehensive.py``.
"""

from __future__ import annotations

import os
import re

import pytest

from app.services.chat_sdk_adapter import build_bound_chat_model


def _openai_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _live_model_name() -> str:
    return (os.getenv("CHAT_SDK_LIVE_OPENAI_MODEL") or "gpt-4o-mini").strip()


@pytest.fixture(scope="module")
def require_openai_key() -> str:
    key = _openai_api_key()
    if not key:
        pytest.skip("Set OPENAI_API_KEY (optional CHAT_SDK_LIVE_OPENAI_MODEL).")
    return key


def test_live_openai_adapter_returns_nonempty_assistant_text(
    require_openai_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", require_openai_key)
    adapter = build_bound_chat_model(
        provider="openai",
        model_name=_live_model_name(),
        with_tools=False,
    )
    turn = adapter.invoke(
        [
            {
                "role": "user",
                "content": (
                    "Reply with exactly one line containing only the word pong "
                    "(lowercase, no punctuation)."
                ),
            }
        ]
    )
    content = (turn.get("content") or "").strip().lower()
    assert content
    assert re.search(r"\bpong\b", content), f"expected pong in reply, got: {content!r}"
    assert not turn.get("tool_calls")


def test_live_openai_lc_human_message_bridge(
    require_openai_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langchain_core")
    from langchain_core.messages import HumanMessage

    monkeypatch.setenv("OPENAI_API_KEY", require_openai_key)
    adapter = build_bound_chat_model(
        provider="openai",
        model_name=_live_model_name(),
        with_tools=False,
    )
    turn = adapter.invoke([HumanMessage(content="Say hi in one word.")])
    assert len((turn.get("content") or "").strip()) >= 1
