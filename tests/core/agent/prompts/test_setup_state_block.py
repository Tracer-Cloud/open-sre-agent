"""The assistant prompt carries the operator's real setup as CONTEXT-tier facts."""

from __future__ import annotations

from core.agent_harness.prompts.assistant import (
    build_assistant_system_prompt_envelope,
)
from core.agent_harness.prompts.kernel.envelope import PromptTier

_SETUP_BLOCK_ID = "assistant-setup-state"
# A value the model could not produce on its own, so finding it in the rendered
# prompt proves it travelled from the caller rather than being invented.
_MARKER = "acme_pager_duty"


def _envelope(setup_state: str, surface: str = "interactive_shell"):
    return build_assistant_system_prompt_envelope(
        reference="",
        history="",
        setup_state=setup_state,
        surface=surface,
    )


def test_setup_state_reaches_the_rendered_prompt() -> None:
    # Arrange / Act
    envelope = _envelope(f"Integrations connected: {_MARKER}\n")

    # Assert
    assert _MARKER in envelope.render()


def test_setup_state_sits_in_the_context_tier() -> None:
    # Arrange / Act: the tier decides cache behaviour, so it is the property
    # under test — setup changes between sessions, not within a turn.
    envelope = _envelope(f"Integrations connected: {_MARKER}\n")

    # Assert
    block = next(b for b in envelope.blocks if b.id == _SETUP_BLOCK_ID)
    assert block.tier is PromptTier.CONTEXT


def test_gateway_surface_does_not_leak_one_installs_setup() -> None:
    # Arrange: a shared chat surface serves many members, so one operator's
    # connected integrations are not theirs to read.
    envelope = _envelope(f"Integrations connected: {_MARKER}\n", surface="gateway")

    # Assert: the marker must not appear anywhere in the gateway prompt.
    assert _MARKER not in envelope.render()


def test_absent_setup_state_adds_no_block() -> None:
    # Arrange / Act: callers that cannot compute the state pass nothing.
    envelope = _envelope("")

    # Assert: no empty header, which would read as "nothing configured".
    assert all(block.id != _SETUP_BLOCK_ID for block in envelope.blocks)


class _StubSession:
    """Minimal session exposing only what the setup-state path reads."""

    def __init__(self, integrations: tuple[str, ...]) -> None:
        self.configured_integrations = integrations
        self.resolved_integrations_cache: dict[str, object] = {}


def test_provider_recomputes_when_an_integration_is_connected(monkeypatch) -> None:
    # Arrange: a session that gains an integration mid-session, as happens when
    # the operator runs the setup wizard from the shell.
    from core.agent_harness.prompts.grounding.provider import DefaultPromptContextProvider

    session = _StubSession(("slack",))
    provider = DefaultPromptContextProvider(session)
    first = provider.setup_state()

    # Act
    session.configured_integrations = ("slack", _MARKER)
    second = provider.setup_state()

    # Assert: a cached first turn would hide the integration just connected.
    assert _MARKER not in first
    assert _MARKER in second


def test_provider_recomputes_when_the_scheduler_store_changes(monkeypatch) -> None:
    # Arrange: integrations are unchanged, so only the store fingerprint moves.
    import platform.setup_state as setup_state
    from core.agent_harness.prompts.grounding.provider import DefaultPromptContextProvider

    provider = DefaultPromptContextProvider(_StubSession(("slack",)))
    counts = iter([0, 7])
    fingerprints = iter([((1, 1), (0, 0), (0, 0)), ((2, 2), (0, 0), (0, 0))])

    def _growing_task_list() -> list[object]:
        return [object()] * next(counts)

    def _moving_fingerprint() -> tuple[tuple[int, int], ...]:
        return next(fingerprints)

    monkeypatch.setattr(setup_state, "_scheduled_tasks", _growing_task_list)
    monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: None)
    monkeypatch.setattr(setup_state, "setup_state_fingerprint", _moving_fingerprint)

    # Act
    first = provider.setup_state()
    second = provider.setup_state()

    # Assert: adding a schedule shows up on the next turn.
    assert "0 configured" in first
    assert "7 configured" in second


def test_headless_provider_reports_no_setup_state() -> None:
    # Arrange: headless runs (scheduled reports, digests) have no operator at a
    # terminal, so there is no setup to narrate.
    from core.agent_harness.turns.headless_adapters import EmptyPromptContextProvider

    # Act / Assert
    assert EmptyPromptContextProvider().setup_state() == ""


def test_shell_provider_delegates_setup_state_to_its_base(monkeypatch) -> None:
    # Arrange: the interactive shell wraps the default provider, so the block
    # has to survive that delegation to reach a real shell turn.
    from surfaces.interactive_shell.grounding import cli_reference as shell_grounding

    def _no_cli_reference(_session: object) -> str:
        return ""

    monkeypatch.setattr(shell_grounding, "session_cli_reference", _no_cli_reference)
    provider = shell_grounding.ShellPromptContextProvider(_StubSession((_MARKER,)))

    # Act
    rendered = provider.setup_state()

    # Assert
    assert _MARKER in rendered
    assert provider.surface() == "interactive_shell"
