"""Live runtime facts must leave the stable system/env prefix."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.prompts.assistant import (
    build_cli_agent_prompt_from_provider,
    build_environment_block,
)
from core.agent_harness.prompts.runtime_facts import (
    render_live_runtime_facts,
    render_static_runtime_facts,
)


def test_static_render_omits_live_keys() -> None:
    runtime = {
        "opensre_version": "0.1",
        "now_iso": "2026-07-11T14:30:12+02:00",
        "uptime_seconds": 42.5,
        "disk_used_percent": 63.2,
        "disk_free_gb": 120.5,
        "memory_used_percent": 41.0,
        "memory_available_gb": 9.4,
        "tz_name": "Europe/Berlin",
    }
    static = render_static_runtime_facts(runtime)
    assert "OpenSRE version is 0.1" in static
    assert "local timezone is Europe/Berlin" in static
    assert "current time is" not in static
    assert "process uptime is" not in static
    assert "root disk is" not in static
    assert "memory is" not in static


def test_live_render_includes_only_live_keys() -> None:
    runtime = {
        "opensre_version": "0.1",
        "now_iso": "2026-07-11T14:30:12+02:00",
        "uptime_seconds": 42.5,
        "disk_used_percent": 63.2,
        "disk_free_gb": 120.5,
        "memory_used_percent": 41.0,
        "memory_available_gb": 9.4,
        "tz_name": "Europe/Berlin",
    }
    live = render_live_runtime_facts(runtime)
    assert "current time is 2026-07-11T14:30:12+02:00" in live
    assert "process uptime is 42.5 seconds" in live
    assert "root disk is 63.2% used with 120.5 GB free" in live
    assert "memory is 41.0% used with 9.4 GB available" in live
    assert "OpenSRE version is" not in live
    assert "local timezone is" not in live  # tz is session-static


def test_environment_block_keeps_static_facts_only() -> None:
    block = build_environment_block(
        integrations=(),
        known=False,
        runtime={
            "opensre_version": "0.1",
            "now_iso": "2026-07-11T14:30:12+02:00",
            "uptime_seconds": 99.0,
            "hostname": "pod-1",
        },
    )
    assert "OpenSRE version is 0.1" in block
    assert "host name is pod-1" in block
    assert "current time is" not in block
    assert "process uptime is" not in block


def test_full_prompt_places_live_facts_immediately_before_user_message() -> None:
    runtime = {
        "opensre_version": "0.1",
        "now_iso": "2026-07-29T22:00:00+00:00",
        "uptime_seconds": 12.0,
        "tz_name": "UTC",
    }

    class _Prompts:
        def surface(self) -> str:
            return "interactive_shell"

        def cli_reference(self) -> str:
            return "ref"

        def agents_md(self) -> str:
            return ""

        def docs(self, query: str) -> str:  # noqa: ARG002 - stub
            return ""

        def investigation_flow(self) -> str:
            return ""

        def runtime_facts(self) -> dict[str, object]:
            return runtime

        def environment_block(self, runtime_facts_in=None) -> str:  # noqa: ANN001
            return build_environment_block(
                integrations=(), known=False, runtime=runtime_facts_in or runtime
            )

        def long_term_memory(self) -> str:
            return ""

        def setup_state(self) -> str:
            return ""

        def suggested_synthetic_prompt(self) -> str:
            return "try again"

        def log_diagnostics(self, reason: str) -> None:  # noqa: ARG002 - stub
            return None

    snap = SimpleNamespace(
        conversation_messages=(),
        configured_integrations_known=True,
        configured_integrations=(),
        last_state=None,
        last_synthetic_observation_path=None,
    )

    prompt = build_cli_agent_prompt_from_provider(
        message="what time is it?",
        prompts=_Prompts(),  # type: ignore[arg-type]
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snap,  # type: ignore[arg-type]
    )

    user_idx = prompt.index("--- User message ---")
    live_idx = prompt.index("current time is 2026-07-29T22:00:00+00:00")
    env_idx = prompt.index("--- Environment (current shell state) ---")
    assert env_idx < live_idx < user_idx
    # Live facts must not appear inside the Environment prefix section.
    env_section = prompt[env_idx:live_idx]
    assert "current time is" not in env_section
    assert "process uptime is" not in env_section


def test_render_captures_runtime_facts_once_and_shares_it() -> None:
    """One capture per turn, shared by the env block and the live block.

    A second capture re-runs the git/importlib probe and re-reads disk+memory
    on every answer turn.
    """
    # Arrange
    facts = {
        "opensre_version": "0.1",
        "hostname": "pod-1",
        "now_iso": "2026-07-29T22:00:00+00:00",
        "uptime_seconds": 12.0,
    }
    captures: list[int] = []
    seen_by_env_block: list[object] = []

    class _CountingPrompts:
        def surface(self) -> str:
            return "interactive_shell"

        def cli_reference(self) -> str:
            return "ref"

        def agents_md(self) -> str:
            return ""

        def docs(self, query: str) -> str:  # noqa: ARG002 - stub
            return ""

        def investigation_flow(self) -> str:
            return ""

        def runtime_facts(self) -> dict[str, object]:
            captures.append(1)
            return facts

        def environment_block(self, runtime=None) -> str:  # noqa: ANN001
            seen_by_env_block.append(runtime)
            return build_environment_block(
                integrations=(), known=False, runtime=runtime or self.runtime_facts()
            )

        def long_term_memory(self) -> str:
            return ""

        def setup_state(self) -> str:
            return ""

        def suggested_synthetic_prompt(self) -> str:
            return ""

        def log_diagnostics(self, reason: str) -> None:  # noqa: ARG002 - stub
            return None

    snap = SimpleNamespace(
        conversation_messages=(),
        configured_integrations_known=True,
        configured_integrations=(),
        last_state=None,
        last_synthetic_observation_path=None,
    )

    # Act
    prompt = build_cli_agent_prompt_from_provider(
        message="hello",
        prompts=_CountingPrompts(),  # type: ignore[arg-type]
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snap,  # type: ignore[arg-type]
    )

    # Assert: captured once, and that same mapping reached the env block.
    assert len(captures) == 1, (
        f"{len(captures)} captures in one turn — the env block and the live "
        "block are each taking their own"
    )
    assert seen_by_env_block == [facts]
    assert "host name is pod-1" in prompt
    assert "current time is 2026-07-29T22:00:00+00:00" in prompt
