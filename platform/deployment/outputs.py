"""Persist EC2 deployment stack outputs under the OpenSRE home directory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR
from platform.deployment.modes import get_stack

_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"


def get_outputs_path(*, path: Path | None = None) -> Path:
    """Return the persisted deployment outputs path."""
    if path is not None:
        return path
    stack = get_stack()
    return _OUTPUTS_DIR / f"{stack.stack_name}.json"


def save_outputs(
    outputs: Mapping[str, Any],
    *,
    path: Path | None = None,
) -> Path:
    """Persist deployment outputs to local user state."""
    stack = get_stack()
    payload = dict(outputs)
    payload.setdefault("StackName", stack.stack_name)

    output_path = get_outputs_path(path=path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_outputs(*, path: Path | None = None) -> dict[str, Any]:
    """Load deployment outputs from local user state."""
    stack = get_stack()
    output_path = get_outputs_path(path=path)
    if not output_path.exists():
        raise FileNotFoundError(
            f"No outputs found for stack '{stack.stack_name}'. Deploy the stack first."
        )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Deployment outputs file is malformed.")
    return result


def delete_outputs(*, path: Path | None = None) -> None:
    """Delete the persisted deployment outputs file."""
    output_path = get_outputs_path(path=path)
    if output_path.exists():
        output_path.unlink()
