from __future__ import annotations

from app.agent.prompt import build_system_prompt


def test_build_system_prompt_non_hermes_uses_generic_category_instruction() -> None:
    prompt = build_system_prompt({"alert_source": "grafana"})

    assert (
        "One of database / infrastructure / code_bug / configuration / network / performance"
        in prompt
    )
    assert "Hermes root cause category taxonomy" not in prompt
    assert "agent_hang" not in prompt


def test_build_system_prompt_hermes_includes_hermes_taxonomy_only() -> None:
    prompt = build_system_prompt({"alert_source": "hermes"})

    assert "Hermes root cause category taxonomy" in prompt
    assert "agent_hang" in prompt
    assert "delivery_hang" in prompt
    assert "ghost_session" in prompt
    assert "connection_exhaustion" not in prompt
