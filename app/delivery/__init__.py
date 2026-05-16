"""Delivery dispatcher — sends investigation results to all configured channels."""

from __future__ import annotations

from typing import Any

from app.state import InvestigationState


def deliver(state: InvestigationState) -> dict[str, Any]:
    """Format and deliver the investigation report to all configured channels.

    Delegates to the existing generate_report implementation which handles
    Slack, Discord, Telegram, GitLab, and terminal rendering.

    Returns state updates with slack_message and report fields.
    """
    from app.delivery.publish_findings.node import generate_report

    state_dict = dict(state)

    if state_dict.get("opensre_evaluate") and state_dict.get("opensre_eval_rubric"):
        from app.integrations.opensre.llm_eval_judge import run_opensre_llm_judge

        try:
            judge_result = run_opensre_llm_judge(
                state=state_dict,
                rubric=state_dict["opensre_eval_rubric"],
            )
            state["opensre_llm_eval"] = judge_result
        except Exception:
            state["opensre_llm_eval"] = {
                "skipped": True,
                "reason": "Judge run failed - check logs for details.",
            }

    return generate_report(state)
