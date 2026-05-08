"""Optional live Anthropic Messages API smoke for ``chat_sdk_adapter``.

Run when ``ANTHROPIC_API_KEY`` is set::

    uv run pytest tests/services/test_chat_sdk_adapter_live_anthropic.py -v

Model default: repo cheap toolcall default (``ANTHROPIC_LLM_CONFIG.toolcall_model``).
Override with ``CHAT_SDK_LIVE_ANTHROPIC_MODEL``.

Scenarios: plain text, system+user split, LangChain human message, short multi-turn
recall, and the empty-after-system guard (no billable API call for the last one).
"""

from __future__ import annotations

import os
import re

import pytest

from app.config import ANTHROPIC_LLM_CONFIG
from app.services.chat_sdk_adapter import build_bound_chat_model


def _anthropic_api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def _live_model_name() -> str:
    override = (os.getenv("CHAT_SDK_LIVE_ANTHROPIC_MODEL") or "").strip()
    if override:
        return override
    return ANTHROPIC_LLM_CONFIG.toolcall_model


@pytest.fixture(scope="module")
def require_anthropic_key() -> str:
    key = _anthropic_api_key()
    if not key:
        pytest.skip("Set ANTHROPIC_API_KEY (optional CHAT_SDK_LIVE_ANTHROPIC_MODEL for model id).")
    return key


def test_live_anthropic_plain_assistant_text(
    require_anthropic_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", require_anthropic_key)
    adapter = build_bound_chat_model(
        provider="anthropic",
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


def test_live_anthropic_leading_system_plus_user(
    require_anthropic_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leading system is lifted to Messages ``system=``; user remains in ``messages``."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", require_anthropic_key)
    adapter = build_bound_chat_model(
        provider="anthropic",
        model_name=_live_model_name(),
        with_tools=False,
    )
    turn = adapter.invoke(
        [
            {"role": "system", "content": "You only reply with the word: ack"},
            {"role": "user", "content": "Follow your instruction."},
        ]
    )
    text = (turn.get("content") or "").strip().lower()
    assert "ack" in text
    assert not turn.get("tool_calls")


def test_live_anthropic_lc_human_message_bridge(
    require_anthropic_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langchain_core")
    from langchain_core.messages import HumanMessage

    monkeypatch.setenv("ANTHROPIC_API_KEY", require_anthropic_key)
    adapter = build_bound_chat_model(
        provider="anthropic",
        model_name=_live_model_name(),
        with_tools=False,
    )
    turn = adapter.invoke([HumanMessage(content="Say hi in one word.")])
    assert len((turn.get("content") or "").strip()) >= 1


def test_live_anthropic_short_multi_turn_recall(
    require_anthropic_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two user turns with an assistant line between — exercises multi-turn normalization.

    Uses a neutral math drill (no 'secret/codeword' phrasing that models may refuse to repeat).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", require_anthropic_key)
    adapter = build_bound_chat_model(
        provider="anthropic",
        model_name=_live_model_name(),
        with_tools=False,
    )
    turn = adapter.invoke(
        [
            {
                "role": "user",
                "content": "Math drill. First integer is 8147 and second is 2201. Reply OK.",
            },
            {"role": "assistant", "content": "OK."},
            {
                "role": "user",
                "content": "What is 8147 + 2201? Reply with only the digits of the sum, no other text.",
            },
        ]
    )
    digits = re.sub(r"\D", "", turn.get("content") or "")
    assert "10348" in digits, f"expected sum 10348 in reply, got: {turn.get('content')!r}"


def test_live_anthropic_system_only_raises_before_api(
    require_anthropic_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter guard — no billable Messages call when only system lines exist."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", require_anthropic_key)
    adapter = build_bound_chat_model(
        provider="anthropic",
        model_name=_live_model_name(),
        with_tools=False,
    )
    with pytest.raises(ValueError, match="empty messages list"):
        adapter.invoke([{"role": "system", "content": "You are a helpful assistant."}])
