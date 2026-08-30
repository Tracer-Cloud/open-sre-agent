"""Ctrl+P toggles the plan overlay only while a plan is on screen."""

from __future__ import annotations

from surfaces.interactive_shell.ui.hooks import install_plan_expand_key_bindings


class _FakeState:
    def __init__(self) -> None:
        self.plan_expanded = False

    def toggle_plan_expanded(self) -> None:
        self.plan_expanded = not self.plan_expanded


def test_ctrl_p_toggles_expansion_and_redraws() -> None:
    # Arrange
    state = _FakeState()
    redraws: list[bool] = []
    kb = install_plan_expand_key_bindings(state, lambda: True, lambda: redraws.append(True))
    handler = kb.bindings[0].handler

    # Act: two presses flip and flip back.
    handler(None)
    handler(None)

    # Assert
    assert state.plan_expanded is False
    assert redraws == [True, True]


def test_binding_is_gated_on_a_plan_being_present() -> None:
    # The filter is False with no plan, so Ctrl+P keeps its default behavior.
    state = _FakeState()
    kb = install_plan_expand_key_bindings(state, lambda: False, lambda: None)

    assert kb.bindings[0].filter() is False
