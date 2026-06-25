from __future__ import annotations

from typing import Any


def test_model_switch_resets_runtime_llm_caches(monkeypatch: Any) -> None:
    import app.cli.interactive_shell.command_registry.model.switching as model_module
    import app.services.agent_llm_client as agent_llm_client
    import app.services.llm_client as llm_client

    calls: list[str] = []

    monkeypatch.setattr(llm_client, "reset_llm_singletons", lambda: calls.append("llm"))
    monkeypatch.setattr(agent_llm_client, "reset_agent_client", lambda: calls.append("agent"))

    model_module._reset_runtime_llm_caches()

    assert calls == ["llm", "agent"]
