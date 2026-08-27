"""Hidden release-artifact smoke command for essential frozen capabilities."""

from __future__ import annotations

import json

import click

_REQUIRED_TOOL_NAMES = frozenset(
    {
        "call_x_tool",
        "execute_python_code",
        "generate_work_status_report",
        "kubernetes_list_pods",
        "query_datadog_all",
        "query_grafana_logs",
        "work_task_add",
    }
)
_REQUIRED_ACTION_SKILL_NAMES = frozenset(
    {
        "architecture-audit",
        "github-ci-fix",
        "github-ci-fix-onboarding",
        "github-security-fix",
        "morning-report",
    }
)
_REQUIRED_INTEGRATION_VERIFIER_NAMES = frozenset({"datadog", "grafana", "x_mcp"})
_GUIDED_TOOL_NAMES = frozenset({"execute_python_code", "generate_work_status_report"})
_ACTION_SKILL_BODY_MARKERS = {"architecture-audit": "REPORT TEMPLATE from"}
_PLANNING_INSTRUCTIONS_MARKER = "PLANNING — update_plan"


@click.command(name="_package-smoke", hidden=True)
def package_smoke_command() -> None:
    """Fail unless essential dynamically bundled code and data are available."""
    from core.agent_harness.spi.grounding import list_action_skills, load_skill_body
    from core.agent_harness.spi.task_plan import load_planning_instructions
    from integrations._verifiers_loader import register_all_verifiers
    from integrations.verification import list_verifiers
    from tools.registry import get_registered_tool_map

    register_all_verifiers()
    tool_map = get_registered_tool_map()
    tool_names = set(tool_map)
    action_skill_names = {skill.name for skill in list_action_skills()}
    integration_verifier_names = set(list_verifiers())
    planning_instructions = load_planning_instructions()

    missing_tools = sorted(_REQUIRED_TOOL_NAMES - tool_names)
    missing_action_skills = sorted(_REQUIRED_ACTION_SKILL_NAMES - action_skill_names)
    missing_action_skill_data = sorted(
        name
        for name, marker in _ACTION_SKILL_BODY_MARKERS.items()
        if marker not in load_skill_body(name)
    )
    missing_integration_verifiers = sorted(
        _REQUIRED_INTEGRATION_VERIFIER_NAMES - integration_verifier_names
    )
    missing_guidance = sorted(
        name
        for name in _GUIDED_TOOL_NAMES
        if name not in tool_map or not tool_map[name].skill_guidance
    )
    failures = {
        "missing_tools": missing_tools,
        "missing_action_skills": missing_action_skills,
        "missing_action_skill_data": missing_action_skill_data,
        "missing_integration_verifiers": missing_integration_verifiers,
        "missing_planning_instructions": (
            [] if _PLANNING_INSTRUCTIONS_MARKER in planning_instructions else ["marker"]
        ),
        "missing_tool_guidance": missing_guidance,
    }
    if any(failures.values()):
        raise click.ClickException(f"PyInstaller package smoke failed: {json.dumps(failures)}")

    click.echo(
        json.dumps(
            {
                "action_skills": len(action_skill_names),
                "integration_verifiers": len(integration_verifier_names),
                "planning_instructions": "ok",
                "registered_tools": len(tool_names),
                "status": "ok",
            },
            sort_keys=True,
        )
    )


__all__ = ["package_smoke_command"]
