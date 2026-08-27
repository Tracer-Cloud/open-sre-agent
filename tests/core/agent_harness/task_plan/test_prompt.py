"""Planning instructions and CURRENT PLAN prompt injection."""

from __future__ import annotations

from core.agent_harness.prompts import (
    PromptBlockId,
    PromptTier,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
)
from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.task_plan.prompt import load_planning_instructions
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _ctx(*, plan=None) -> TurnSnapshot:
    return TurnSnapshot(
        text="investigate checkout 502s",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        task_plan=plan,
    )


def test_planning_instructions_carry_the_dense_diagnosis_exemplar() -> None:
    text = load_planning_instructions()
    lines = text.splitlines()
    assert 60 <= len(lines) <= 200
    assert "update_plan" in text
    assert "ASK THEN PLAN" in text
    assert "ask_user_choice" in text
    assert "go-ahead to continue" in text
    assert "do not invent a pause" in text.lower()
    assert "VERIFIABILITY" in text
    assert "Confirm checkout returns 2xx" in text
    assert "work_task_*" in text
    assert "/goal" in text
    # Dense diagnosis is required for incident workloads; guard the exemplar
    # and the scope fence so ordinary implementation work is not forced into
    # fabricated Facts / hypothesis prose.
    assert "GOOD EXPLANATION" in text
    assert "What the signature tells us" in text
    assert "Discriminator" in text
    assert "EXPLANATION — match the workload" in text
    assert "Do not fabricate telemetry" in text
    assert "ordinary plan-only" in text.lower()


def test_composed_prompt_includes_planning_instructions() -> None:
    prompt = build_action_system_prompt(_ctx())
    assert "PLANNING — update_plan" in prompt
    assert "The LAST step is always a verification step" in prompt


def test_current_plan_is_ephemeral_so_compaction_cannot_drop_it() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Run /health and read the result", "status": "completed"},
                {"step": "List connected integrations", "status": "in_progress"},
                {"step": "Confirm both outputs answered the ask", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    envelope = build_action_system_prompt_envelope(_ctx(plan=plan))
    block = envelope.require_block(PromptBlockId.CURRENT_TASK_PLAN)
    assert block.tier == PromptTier.EPHEMERAL
    assert "Plan · 2/3" in block.content
    assert "CURRENT PLAN" in block.content
    assert "Do not conclude this turn while a step is in_progress" in block.content
    cached = envelope.render_cached()
    assert "Plan · 2/3" not in cached
    assert "Plan · 2/3" in envelope.render()


def test_ask_user_answers_inject_start_now_block() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_GUIDANCE

    answers = format_ask_user_answers(
        (
            AskUserQuestion(label="Env", title="Where is it?", options=("Prod", "Dev")),
            AskUserQuestion(label="Window", title="What window?", options=("24h", "7d")),
        ),
        ("Dev", "24h"),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )
    envelope = build_action_system_prompt_envelope(snapshot)
    block = envelope.require_block(PromptBlockId.ASK_USER_ANSWERED)
    assert block.tier == PromptTier.EPHEMERAL
    assert ASK_USER_ANSWERED_GUIDANCE in block.content
    assert ASK_USER_ANSWERED_GUIDANCE not in envelope.render_cached()
    assert ASK_USER_ANSWERED_GUIDANCE in envelope.render()
    assert envelope.block(PromptBlockId.ASK_USER_ANSWERED) is not None
    idle = build_action_system_prompt_envelope(_ctx())
    assert idle.block(PromptBlockId.ASK_USER_ANSWERED) is None


def test_ask_user_answered_guidance_defaults_to_execute_not_pause() -> None:
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_GUIDANCE

    text = ASK_USER_ANSWERED_GUIDANCE.lower()
    assert "go-ahead to continue" in text
    assert "do not invent a plan-only pause" in text
    assert "plan_only_after=true" in text
    assert "in_progress and execute it now" in text


def test_ask_user_answered_guidance_scopes_diagnosis_shape_to_incidents() -> None:
    from core.agent_harness.task_plan.prompt import (
        ASK_USER_ANSWERED_GUIDANCE,
        ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE,
    )

    for text in (ASK_USER_ANSWERED_GUIDANCE, ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE):
        assert "If this is a diagnosis" in text
        assert "If this is implementation or plan-only coding work" in text
        assert "do not invent telemetry" in text.lower()


def test_ask_user_answers_preserve_original_repo_and_all_requested_metrics() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )

    original = "For facebook/react, return merged PR count, median time-to-merge, and star gain."
    answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which date window?", options=("7d", "30d")),),
        ("7d",),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(("user", original),),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )

    rendered = build_action_system_prompt_envelope(snapshot).render()

    assert original in rendered
    assert "preserve the original target repository" in rendered
    assert "every requested output or metric" in rendered
    assert "Q&A answers refine that request; they never replace it" in rendered


def test_ask_user_answered_plan_only_guidance_does_not_authorize_execute() -> None:
    from core.agent_harness.session.pending_choice import (
        AskUserQuestion,
        format_ask_user_answers,
    )
    from core.agent_harness.task_plan.prompt import ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE

    answers = format_ask_user_answers(
        (
            AskUserQuestion(label="Env", title="Where is it?", options=("Prod", "Dev")),
            AskUserQuestion(label="Window", title="What window?", options=("24h", "7d")),
        ),
        ("Dev", "24h"),
    )
    snapshot = TurnSnapshot(
        text=answers,
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
        plan_only_until_authorized=True,
    )
    envelope = build_action_system_prompt_envelope(snapshot)
    block = envelope.require_block(PromptBlockId.ASK_USER_ANSWERED)
    assert ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE in block.content
    assert "do not pass plan_only=false" in block.content.lower()
    assert "in_progress and execute it now" not in block.content.lower()


def test_current_task_plan_block_is_empty_without_steps() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    assert current_task_plan_block(None) == ""
    empty, error = parse_task_plan({"plan": [{"step": "x", "status": "pending"}]})
    assert error is not None
    assert current_task_plan_block(empty) == ""


def test_current_task_plan_block_plan_only_does_not_authorize_execution() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "pending"},
                {"step": "Confirm the workflow is green", "status": "pending"},
            ],
            "explanation": "do not run yet",
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan, plan_only=True)
    assert "CURRENT PLAN (ready, nothing executed" in block
    assert "explanation: do not run yet" in block
    assert "Execution is authorized" not in block


def test_current_task_plan_block_all_pending_without_latch_authorizes() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "pending"},
                {"step": "Confirm the workflow is green", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan, plan_only=False)
    assert "Execution is authorized" in block


def test_current_task_plan_block_completed_status() -> None:
    from core.agent_harness.task_plan.prompt import current_task_plan_block

    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the failing job", "status": "completed"},
                {"step": "Confirm the workflow is green", "status": "completed"},
            ]
        }
    )
    assert error is None and plan is not None
    block = current_task_plan_block(plan)
    assert "CURRENT PLAN (complete" in block
    assert "in_progress" not in block
