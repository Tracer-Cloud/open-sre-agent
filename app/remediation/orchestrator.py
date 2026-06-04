from __future__ import annotations

import logging
from typing import Any

from app.remediation.classifier import classify_remediation_steps
from app.remediation.executor import execute_remediation_action
from app.remediation.models import (
    RemediationAction,
    RemediationResult,
    SafetyLevel,
)

logger = logging.getLogger(__name__)


def run_remediation_plan(
    steps: list[str],
    *,
    auto_execute: bool = False,
    confirm_fn: Any | None = None,
) -> dict[str, Any]:
    actions = classify_remediation_steps(steps)
    classified: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    auto_succeeded = True

    for action in actions:
        entry = _action_to_dict(action)
        classified.append(entry)

        if action.safety_level is SafetyLevel.manual:
            results.append(
                {
                    "action": entry,
                    "success": False,
                    "output": "",
                    "error": "Manual step — execute manually",
                    "skipped": True,
                }
            )
            continue

        if action.safety_level is SafetyLevel.safe or auto_execute:
            result = execute_remediation_action(action)
            entry_result = _result_to_dict(result)
            results.append(entry_result)
            if not result.success:
                auto_succeeded = False
        else:
            if confirm_fn is not None:
                confirmed = confirm_fn(action)
                if confirmed:
                    result = execute_remediation_action(action)
                    entry_result = _result_to_dict(result)
                    results.append(entry_result)
                    if not result.success:
                        auto_succeeded = False
                else:
                    results.append(
                        {
                            "action": entry,
                            "success": False,
                            "output": "",
                            "error": "Rejected by user",
                            "skipped": True,
                        }
                    )
            else:
                results.append(
                    {
                        "action": entry,
                        "success": False,
                        "output": "",
                        "error": "Requires confirmation — no confirm_fn provided",
                        "skipped": True,
                    }
                )

    return {
        "remediation_plan": classified,
        "remediation_results": results,
        "auto_execute": auto_execute,
        "all_succeeded": all(r.get("success") for r in results if not r.get("skipped")),
        "auto_succeeded": auto_succeeded,
    }


def _action_to_dict(action: RemediationAction) -> dict[str, Any]:
    return {
        "action_type": str(action.action_type),
        "description": action.description,
        "command": action.command,
        "safety_level": str(action.safety_level),
        "target": action.target,
    }


def _result_to_dict(result: RemediationResult) -> dict[str, Any]:
    return {
        "action": _action_to_dict(result.action),
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }
