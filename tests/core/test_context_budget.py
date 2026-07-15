from __future__ import annotations

from core.context_budget import context_budget_ceiling_for_model, strip_internal_message_markers


class TestGpt56ContextWindow:
    """GPT-5.6 ships a 1M-token window, unlike the 128k-pinned gpt-5 family (#3931)."""

    def test_sol_reclaims_the_million_token_window(self) -> None:
        # 1_000_000 window - 16_000 response headroom.
        assert context_budget_ceiling_for_model("gpt-5.6-sol") == 984_000

    def test_all_tiers_share_the_family_window(self) -> None:
        sol = context_budget_ceiling_for_model("gpt-5.6-sol")
        assert context_budget_ceiling_for_model("gpt-5.6-terra") == sol
        assert context_budget_ceiling_for_model("gpt-5.6-luna") == sol
        assert context_budget_ceiling_for_model("gpt-5.6") == sol

    def test_gpt56_is_not_shadowed_by_the_gpt5_catch_all(self) -> None:
        # ``_MODEL_CONTEXT_WINDOWS`` is matched by substring in insertion
        # order with a break, so moving the ``gpt-5.6`` key below ``gpt-5``
        # would silently pin 5.6 back to 128k. Nothing else would fail.
        assert context_budget_ceiling_for_model("gpt-5.6-sol") > context_budget_ceiling_for_model(
            "gpt-5.5"
        )

    def test_older_gpt5_models_keep_their_conservative_pin(self) -> None:
        # 128_000 window - 16_000 response headroom.
        assert context_budget_ceiling_for_model("gpt-5.5") == 112_000
        assert context_budget_ceiling_for_model("gpt-5") == 112_000


def test_strip_internal_message_markers_removes_opensre_keys() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "seed", "content": "ok"}],
            "_opensre_seed": True,
            "_opensre_duplicate_result": True,
        },
    ]

    cleaned = strip_internal_message_markers(messages)

    assert cleaned[0] == messages[0]
    assert cleaned[1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
    }
    assert cleaned[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "seed", "content": "ok"}],
    }
    assert messages[1]["_opensre_seed"] is True
    assert messages[2]["_opensre_duplicate_result"] is True


def test_strip_internal_message_markers_preserves_other_underscore_keys() -> None:
    messages = [{"role": "user", "content": "hi", "_custom": "keep"}]

    cleaned = strip_internal_message_markers(messages)

    assert cleaned == [{"role": "user", "content": "hi", "_custom": "keep"}]
