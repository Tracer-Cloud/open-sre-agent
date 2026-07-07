"""Guards for the stale-process failure mode around the LLM client graph.

Covers the two hardenings: eager preload of the ``core.llm`` client modules, and
the actionable message shown when the reasoning-client import fails.
"""

from __future__ import annotations

import sys

from core.agent_harness.providers.default_providers import (
    DefaultReasoningClientProvider,
    _llm_client_unavailable_message,
)
from core.llm.internal.preload import preload_llm_clients


def test_preload_imports_the_llm_client_graph() -> None:
    preload_llm_clients()
    assert "core.llm.factory" in sys.modules
    assert "core.llm.transports.sdk.agent_clients" in sys.modules
    assert "core.llm.transports.sdk.llm_clients" in sys.modules
    # transport_mode is pulled in transitively — the whole graph is one snapshot.
    assert "core.llm.transport_mode" in sys.modules


def test_preload_is_idempotent() -> None:
    preload_llm_clients()
    preload_llm_clients()  # must not raise on a second call


def test_import_error_message_hints_at_restart() -> None:
    message = _llm_client_unavailable_message(ImportError("cannot import name 'x'"))
    assert "cannot import name 'x'" in message
    assert "Restart it" in message
    assert "uv run opensre" in message


def test_non_import_error_message_is_unchanged() -> None:
    message = _llm_client_unavailable_message(ValueError("boom"))
    assert message == "LLM client unavailable: boom"
    assert "Restart" not in message


def test_reasoning_provider_renders_actionable_message_on_import_error(monkeypatch) -> None:
    rendered: list[str] = []

    class FakeOutput:
        def render_error(self, message: str) -> None:
            rendered.append(message)

    # Force the lazy import inside get() to fail like a stale process would.
    monkeypatch.setitem(sys.modules, "core.llm.factory", None)

    provider = DefaultReasoningClientProvider(output=FakeOutput())
    result = provider.get()

    assert result is None
    assert rendered and "Restart it" in rendered[0]
