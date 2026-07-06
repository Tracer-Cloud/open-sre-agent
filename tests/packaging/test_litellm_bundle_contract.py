"""Regression tests for LiteLLM package data in frozen release binaries.

Azure OpenAI and ``OPENSRE_LLM_TRANSPORT=litellm`` import ``litellm`` at runtime.
LiteLLM reads JSON price/context files from its package directory on import; the
release PyInstaller build must bundle them under ``_internal/litellm/`` or the
binary crashes with ``FileNotFoundError`` (see issue #3631).
"""

from __future__ import annotations

from pathlib import Path

import litellm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"

# LiteLLM loads this file during import; it must be present in frozen bundles.
_REQUIRED_LITELLM_DATA_FILE = "model_prices_and_context_window_backup.json"


def test_litellm_package_ships_required_price_context_backup() -> None:
    """Dev installs must still expose LiteLLM's price/context JSON (sanity check)."""
    data_path = Path(litellm.__file__).parent / _REQUIRED_LITELLM_DATA_FILE
    assert data_path.is_file(), f"expected LiteLLM data file at {data_path}"


def test_release_workflow_collects_litellm_package_data() -> None:
    """The release build must collect LiteLLM package data for frozen binaries."""
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "--collect-data litellm" in workflow
    assert "model_prices_and_context_window_backup.json" in workflow
