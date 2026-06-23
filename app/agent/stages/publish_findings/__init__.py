"""Publish findings node — format and deliver investigation reports."""

from __future__ import annotations

from typing import Any

from app.agent.stages.publish_findings.evaluation import run_optional_opensre_evaluation
from app.agent.stages.publish_findings.node import generate_report
from app.state import InvestigationState


def deliver(state: InvestigationState) -> dict[str, Any]:
    """Format and deliver the investigation report to all configured channels.

    Returns state updates with slack_message and report fields.
    """
    state_dict = dict(state)
    extra_updates = run_optional_opensre_evaluation(state_dict)
    return {**generate_report(state), **extra_updates}


__all__ = [
    "deliver",
    "generate_report",
]
