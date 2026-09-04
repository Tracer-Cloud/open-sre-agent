"""Hidden release-artifact smoke command for essential frozen capabilities."""

from __future__ import annotations

import json
import sys

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


@click.command(name="_package-smoke", hidden=True)
def package_smoke_command() -> None:
    """Fail unless essential dynamically bundled code and data are available."""
    from core.agent_harness.spi.grounding import list_action_skills, load_skill_body
    from integrations._verifiers_loader import register_all_verifiers
    from integrations.verification import list_verifiers
    from tools.registry import get_registered_tool_map
    from tools.registry_index import BAKED_INDEX_RELATIVE_PATH, baked_index_available

    # Frozen builds silently fall back to importing every vendor module when
    # the bake is missing. Fail before the slow path so release smoke cannot
    # pass on an artifact that restored the first-turn delay.
    frozen = bool(getattr(sys, "frozen", False))
    if frozen and not baked_index_available():
        baked_failures = {"missing_baked_descriptor_index": [BAKED_INDEX_RELATIVE_PATH.as_posix()]}
        raise click.ClickException(
            f"PyInstaller package smoke failed: {json.dumps(baked_failures)}"
        )

    register_all_verifiers()
    tool_map = get_registered_tool_map()
    tool_names = set(tool_map)
    action_skill_names = {skill.name for skill in list_action_skills()}
    integration_verifier_names = set(list_verifiers())

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
        "missing_tool_guidance": missing_guidance,
    }
    if any(failures.values()):
        raise click.ClickException(f"PyInstaller package smoke failed: {json.dumps(failures)}")

    payload: dict[str, object] = {
        "action_skills": len(action_skill_names),
        "integration_verifiers": len(integration_verifier_names),
        "registered_tools": len(tool_names),
        "status": "ok",
    }
    if frozen:
        payload["baked_descriptor_index"] = True
    click.echo(json.dumps(payload, sort_keys=True))


__all__ = ["package_smoke_command"]
