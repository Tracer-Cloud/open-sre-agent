"""Contracts for deterministic code and data inputs in frozen releases."""

from __future__ import annotations

from pathlib import Path

from platform.packaging.release_manifest import (
    required_skill_files,
    runtime_hidden_imports,
)
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_SPEC_FILE = _REPO_ROOT / "opensre.spec"


def test_hidden_imports_cover_runtime_discovered_tool_packages() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))

    assert set(INTEGRATION_TOOL_PACKAGES) <= hidden_imports
    assert "integrations.x_mcp.tools.x_mcp_tool" in hidden_imports
    assert "tools.system.work_items" in hidden_imports
    assert "tools.system.work_items.tool" in hidden_imports


def test_hidden_imports_exclude_non_runtime_discovery_modules() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))

    assert "tools.registry" not in hidden_imports
    assert "tools.investigation_registry" not in hidden_imports
    assert "tools.investigation_registry.prioritization" not in hidden_imports


def test_hidden_imports_cover_runtime_discovered_integration_verifiers() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))
    verifier_modules = {
        ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)
        for path in (_REPO_ROOT / "integrations").glob("*/verifier.py")
    }

    assert verifier_modules <= hidden_imports


def test_required_skill_data_covers_action_and_tool_guidance() -> None:
    relative_paths = {
        path.relative_to(_REPO_ROOT).as_posix() for path in required_skill_files(_REPO_ROOT)
    }

    assert "core/agent_harness/prompts/skills/architecture_audit/SKILL.md" in relative_paths
    assert (
        "core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md"
        in relative_paths
    )
    assert "integrations/github/tools/workflow/SKILL.md" in relative_paths
    assert "integrations/sentry/tools/skills/sentry-summary/SKILL.md" in relative_paths
    assert (
        "tools/system/python_execution_tool/skills/github-star-velocity/SKILL.md" in relative_paths
    )


def test_release_build_uses_checked_in_spec() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    spec = _SPEC_FILE.read_text(encoding="utf-8")

    assert "uv run pyinstaller opensre.spec" in workflow
    assert "OPENSRE_PYINSTALLER_MODE: ${{ matrix.pyinstaller_mode }}" in workflow
    assert "release_manifest.py" in spec
    assert "skill_data_entries(ROOT)" in spec
