"""Headless scheduled prompt runner for manual loops."""

from __future__ import annotations

import logging

from core.agent_harness.harness import AgentSession
from platform.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_MANUAL_LOOP_INSTRUCTIONS = """Scheduled report loop.

Produce only the report body requested below.
Do not start an RCA, incident investigation, alert triage, diagnosis, or remediation workflow.
Do not mention incidents, severity, hypotheses, recommended actions, or follow-up questions
unless the report request explicitly asks for those sections.
Do not send, post, notify, or message any channel from inside this turn; the scheduler
will deliver the final report body to the configured channels after this runner returns.
Use read-only tools when data is required. For GitHub star history, call
get_github_star_history and compute "Stars Gained" from the returned daily rows.
"""


def build_manual_loop_prompt(payload: AgentPayload) -> str:
    """Build the headless report prompt for a manual loop payload."""
    prompt = str(
        payload.get("loop_prompt") or payload.get("prompt") or payload.get("description") or ""
    ).strip()
    if not prompt:
        raise RuntimeError("Manual loop prompt is empty.")

    name = str(payload.get("name") or payload.get("task_name") or "manual loop").strip()
    return f"{_MANUAL_LOOP_INSTRUCTIONS}\nLoop name: {name}\n\nReport request:\n{prompt}"


def run_manual_prompt_loop(payload: AgentPayload) -> str:
    """Run one headless turn that produces only the requested report body."""
    message = build_manual_loop_prompt(payload)
    result = AgentSession.run_headless_turn(
        message,
        logger=logger,
        gather_enabled=True,
        is_tty=False,
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError("Manual loop failed: the reasoning client did not produce a report.")
    return report


__all__ = ["build_manual_loop_prompt", "run_manual_prompt_loop"]
