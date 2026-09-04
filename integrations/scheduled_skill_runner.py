"""Headless scheduled runner for pinned recurring action skills."""

from __future__ import annotations

import logging

from core.agent_harness import (
    AgentSession,
    resolve_scheduled_skill,
    scheduled_skill_context_block,
)
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_SCHEDULED_SKILL_INSTRUCTIONS = """Scheduled recurring skill run.

Follow the skill recipe below for this unattended tick, using any
pre-fetched data as the source of truth.
Produce only the final report body text the scheduler should deliver.
Do not send, post, notify, or message any channel from inside this turn; the
scheduler will deliver the final report body to the configured channels after
this runner returns.
Do not call propose_scheduled_delivery or offer to schedule again.
Use read-only tools only. Do not run shell commands, mutate GitHub issues,
or change any external system.
"""


def _prefetched_context(skill_name: str, inputs: dict[str, str]) -> str:
    """Fetch integration-owned context without reversing core dependency direction."""
    if skill_name == "github-ci-health":
        from integrations.github.ci_health_runner import run_github_ci_health

        return run_github_ci_health(inputs)
    return scheduled_skill_context_block(skill_name, inputs)


def run_scheduled_recurring_skill(payload: AgentPayload) -> str:
    """Run one headless turn for a pinned recurring skill and return report text."""
    resolved = resolve_scheduled_skill(
        str(payload.get("skill_name") or ""),
        str(payload.get("skill_revision") or ""),
    )
    raw_inputs = payload.get("skill_inputs") or {}
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    input_block = ""
    if inputs:
        rendered = "\n".join(f"- {key}: {value}" for key, value in sorted(inputs.items()))
        input_block = f"\nValidated inputs:\n{rendered}\n"
    typed_inputs = {str(key): str(value) for key, value in inputs.items()}
    fetch_block = _prefetched_context(resolved.name, typed_inputs)
    fetch_section = f"\n{fetch_block}\n" if fetch_block else ""
    message = (
        f"{_SCHEDULED_SKILL_INSTRUCTIONS}\n"
        f"Skill: {resolved.name}\n"
        f"{input_block}"
        f"{fetch_section}\n"
        f"Skill recipe:\n{resolved.body}"
    )
    result = AgentSession.run_headless_turn(
        message,
        logger=logger,
        is_tty=False,
        unattended=True,
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError(
            f"Scheduled skill {resolved.name!r} failed: "
            "the reasoning client did not produce a report."
        )
    return report


__all__ = ["run_scheduled_recurring_skill"]
