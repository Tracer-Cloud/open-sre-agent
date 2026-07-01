"""Persist gateway deployment stack outputs under the OpenSRE home directory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR

STACK_NAME = "opensre-ec2-gateway"
_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"
_OUTPUTS_FILE = f"{STACK_NAME}.json"


def get_outputs_path(path: Path | None = None) -> Path:
    """Return the persisted gateway deployment outputs path."""
    return path or (_OUTPUTS_DIR / _OUTPUTS_FILE)


def save_outputs(outputs: Mapping[str, Any], *, path: Path | None = None) -> Path:
    """Persist gateway deployment outputs to local user state."""
    output_path = get_outputs_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(outputs), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_outputs(*, path: Path | None = None) -> dict[str, Any]:
    """Load gateway deployment outputs from local user state."""
    output_path = get_outputs_path(path)
    if not output_path.exists():
        raise FileNotFoundError(
            f"No outputs found for stack '{STACK_NAME}'. Deploy the stack first."
        )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Gateway deployment outputs file is malformed.")
    return result


def delete_outputs(*, path: Path | None = None) -> None:
    """Delete the persisted gateway deployment outputs file."""
    output_path = get_outputs_path(path)
    if output_path.exists():
        output_path.unlink()
