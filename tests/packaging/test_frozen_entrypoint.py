"""The frozen binary starts where the ``opensre`` console script starts.

A PyInstaller build entered through ``surfaces/cli`` alone would run a CLI with
no :class:`~surfaces.cli.host.CliHost`: bare ``opensre`` would print the landing
page instead of opening the shell, ``onboard`` would not hand off to it, and
``gateway start --foreground`` would fail. The spec and ``pyproject`` must name
the same entry.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT_MODULE = "surfaces.entrypoint"
_ENTRYPOINT_SOURCE = "surfaces/entrypoint.py"


def test_console_script_points_at_the_composition_entrypoint() -> None:
    # Arrange
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    # Act
    console_script = pyproject["project"]["scripts"]["opensre"]

    # Assert
    assert console_script == f"{_ENTRYPOINT_MODULE}:main"


def test_pyinstaller_spec_analyses_the_same_entrypoint() -> None:
    # Arrange
    spec = (REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    # Act: the single script passed to Analysis(...).
    match = re.search(r"a = Analysis\(\s*\[str\(ROOT / \"([^\"]+)\"\)\]", spec)

    # Assert
    assert match is not None, "could not find the Analysis entry script in opensre.spec"
    assert match.group(1) == _ENTRYPOINT_SOURCE


def test_package_module_runner_uses_the_entrypoint() -> None:
    """``python -m surfaces`` is the whole product, like the console script."""
    # Arrange / Act
    source = (REPO_ROOT / "surfaces/__main__.py").read_text(encoding="utf-8")

    # Assert
    assert f"from {_ENTRYPOINT_MODULE} import main" in source


def test_frozen_bundle_ships_the_shared_surface_data() -> None:
    """``surfaces/shared`` holds the bundled demo alert the CLI and shell offer."""
    # Arrange
    spec = (REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    # Act / Assert
    assert 'collect_data_files("surfaces.shared")' in spec
    assert (REPO_ROOT / "surfaces/shared/sample_alerts/alert.json").is_file()


def test_frozen_bundle_ships_the_shared_system_prompt() -> None:
    """The shared prompt loader reads its adjacent Markdown at runtime."""
    spec = (REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    assert '"core.agent_harness.prompts"' in spec
    assert 'includes=["opensre_system_prompt.md"]' in spec
    assert (REPO_ROOT / "core/agent_harness/prompts/opensre_system_prompt.md").is_file()


def test_release_artifacts_ship_the_planning_instructions() -> None:
    """The task-plan prompt loader reads its adjacent Markdown at runtime."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data["core.agent_harness.task_plan"] == ["planning_instructions.md"]
    assert (REPO_ROOT / "core/agent_harness/task_plan/planning_instructions.md").is_file()
