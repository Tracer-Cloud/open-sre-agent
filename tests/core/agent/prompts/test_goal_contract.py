"""Goal contract — the STABLE lines must encode the right conditions.

These tests are not live LLM A/Bs. They pin that the contract text still makes
sense as a policy: capacity from setup_state, offer via propose/cron, no invent,
no nag, shell-only for the assistant closer bias.
"""

from __future__ import annotations

import re

from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.action.text import (
    _SYSTEM_PROMPT_BASE,
    ACTION_SETUP_CAPACITY_SCHEDULE_RULE,
)
from core.agent_harness.prompts.assistant.text import (
    CLI_PREAMBLE,
    GATEWAY_PREAMBLE,
    SHELL_GOAL_CONTRACT,
)
from core.agent_harness.prompts.kernel.envelope import PromptTier
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _mentions(text: str, *needles: str) -> None:
    lower = text.lower()
    missing = [n for n in needles if n.lower() not in lower]
    assert not missing, f"missing {missing!r} in:\n{text}"


def test_shell_goal_contract_is_capacity_gated_not_a_playbook() -> None:
    # Arrange / Act
    contract = SHELL_GOAL_CONTRACT

    # Assert: conditions, not morning_report STEPS
    _mentions(
        contract,
        "setup-state",
        "connected integration",
        "schedule",
        "do not nag",
        "never invent",
    )
    assert "shell_run" not in contract.lower()
    assert "wttr.in" not in contract.lower()
    # Keep it short — fat STABLE is the failure mode.
    assert len(contract) < 500


def test_shell_goal_contract_lives_in_cli_preamble_not_gateway() -> None:
    # Arrange / Act / Assert: gateway has no setup_state; do not push schedule offers there.
    assert SHELL_GOAL_CONTRACT in CLI_PREAMBLE
    assert SHELL_GOAL_CONTRACT not in GATEWAY_PREAMBLE
    assert "setup-state block" not in GATEWAY_PREAMBLE.lower()


def test_action_capacity_rule_ties_facts_to_propose_not_skip() -> None:
    # Arrange / Act
    rule = ACTION_SETUP_CAPACITY_SCHEDULE_RULE

    # Assert: read facts → propose when capacity; empty integrations → no invent
    _mentions(
        rule,
        "setup-state",
        "Integrations connected",
        "none",
        "propose_scheduled_delivery",
        "WAIT",
        "schedule_count",
        "/integrations setup",
    )
    assert "do not invent" in rule.lower() or "not invent" in rule.lower()
    # Must not tell the planner to skip offers merely because schedules exist.
    assert re.search(r"do not skip the offer only because schedule_count", rule, re.I)
    assert len(rule) < 600


def test_action_capacity_rule_is_wired_into_stable_system_prompt() -> None:
    # Arrange / Act
    envelope = build_action_system_prompt_envelope(
        TurnSnapshot(
            text="morning report",
            conversation_messages=(),
            configured_integrations=("slack",),
            configured_integrations_known=True,
            last_state=None,
            last_synthetic_observation_path=None,
            reasoning_effort=None,
            setup_state=(
                "--- Setup state ---\n"
                "Integrations connected: slack\n"
                "Scheduled tasks configured: 0\n"
                "Last scheduled delivery: never run\n\n"
            ),
        )
    )

    # Assert
    assert ACTION_SETUP_CAPACITY_SCHEDULE_RULE in _SYSTEM_PROMPT_BASE
    assert ACTION_SETUP_CAPACITY_SCHEDULE_RULE in envelope.render_cached()
    stable = "".join(b.content for b in envelope.blocks if b.tier is PromptTier.STABLE)
    assert "propose_scheduled_delivery" in stable
    # Facts stay CONTEXT; guidance stays STABLE.
    context = "".join(b.content for b in envelope.blocks if b.tier is PromptTier.CONTEXT)
    assert "Scheduled tasks configured: 0" in context
    assert ACTION_SETUP_CAPACITY_SCHEDULE_RULE not in context


def test_goal_contract_does_not_belong_in_setup_state_facts() -> None:
    # Arrange: render_setup_state must stay guidance-free (plan invariant).
    from platform.setup_state import SetupSnapshot, render_setup_state

    # Act
    facts = render_setup_state(
        SetupSnapshot(integrations=("slack",), schedule_count=0, last_delivery_ok=None)
    )

    # Assert
    assert "propose_scheduled_delivery" not in facts
    assert "do not nag" not in facts.lower()
    assert "Integrations connected: slack" in facts
