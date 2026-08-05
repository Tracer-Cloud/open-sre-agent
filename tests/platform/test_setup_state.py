"""Tests for the user-facing setup state surfaced to the assistant prompt."""

from __future__ import annotations

from platform.setup_state import SetupSnapshot, render_setup_state


class TestRenderSetupState:
    def test_reports_connected_integrations_and_schedule_count(self) -> None:
        # Arrange: a configured install with one live schedule.
        state = SetupSnapshot(
            integrations=("posthog_mcp", "slack"),
            schedule_count=2,
            last_delivery_ok=True,
        )

        # Act
        rendered = render_setup_state(state)

        # Assert: the facts the assistant needs to ground a next step.
        assert "posthog_mcp" in rendered
        assert "slack" in rendered
        assert "2" in rendered

    def test_names_the_empty_install_explicitly(self) -> None:
        # Arrange: a first-run user — nothing connected, nothing scheduled.
        state = SetupSnapshot(integrations=(), schedule_count=0, last_delivery_ok=None)

        # Act
        rendered = render_setup_state(state)

        # Assert: "none" must be stated, not left absent. An omitted line reads
        # as "unknown" and the model fills the gap by guessing.
        assert "none" in rendered.lower()
        assert "never" in rendered.lower()

    def test_states_facts_without_instructing_the_model(self) -> None:
        # Arrange: the block is a CONTEXT-tier fact sheet, not a rule block.
        state = SetupSnapshot(integrations=("slack",), schedule_count=0, last_delivery_ok=None)

        # Act
        rendered = render_setup_state(state)

        # Assert: no imperative guidance leaks in. Instructions belong to the
        # STABLE persona; mixing them here means a per-turn fact change would
        # silently rewrite the rules.
        lowered = rendered.lower()
        for imperative in ("you should", "offer to", "suggest that", "always", "must"):
            assert imperative not in lowered

    def test_failed_last_delivery_is_distinguished_from_never_run(self) -> None:
        # Arrange: a schedule that ran and failed is not the same as one that
        # has never fired — conflating them hides a broken delivery.
        failed = SetupSnapshot(integrations=("slack",), schedule_count=1, last_delivery_ok=False)
        never = SetupSnapshot(integrations=("slack",), schedule_count=1, last_delivery_ok=None)

        # Act
        failed_text = render_setup_state(failed).lower()
        never_text = render_setup_state(never).lower()

        # Assert
        assert failed_text != never_text
        assert "failed" in failed_text
        assert "never" in never_text


class TestCollectSetupState:
    def test_pairs_caller_integrations_with_live_schedules(self, monkeypatch) -> None:
        # Arrange: the caller owns the integration list because the session
        # already holds the hydrated names.
        import platform.setup_state as setup_state

        def _tasks() -> list[object]:
            return [object(), object()]

        monkeypatch.setattr(setup_state, "_scheduled_tasks", _tasks)
        monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: True)

        # Act
        state = setup_state.collect_setup_state(("slack", "posthog_mcp"))

        # Assert
        assert state.integrations == ("slack", "posthog_mcp")
        assert state.schedule_count == 2
        assert state.last_delivery_ok is True

    def test_unreadable_scheduler_keeps_the_integrations_it_was_given(self, monkeypatch) -> None:
        # Arrange: a fresh install where the scheduler store does not exist yet.
        import platform.setup_state as setup_state

        def _boom() -> list[object]:
            raise OSError("no store on a fresh install")

        monkeypatch.setattr(setup_state, "_scheduled_tasks", _boom)

        # Act: prompt assembly runs every turn, so this may not raise.
        state = setup_state.collect_setup_state(("slack",))

        # Assert: an unreadable scheduler must not erase a known integration --
        # reporting "none" would tell the agent nothing is connected.
        assert state.integrations == ("slack",)
        assert state.schedule_count == 0
        assert state.last_delivery_ok is None
