from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.orcabench.host.launcher import (
    AGENT_IMPORT_PATH,
    DEFAULT_DATASET,
    _opensre_repo_root,
    _validate_exact_task_name,
    build_harbor_command,
)


def test_opensre_repo_root_contains_importable_tests_package() -> None:
    root = _opensre_repo_root()

    assert (root / "tests" / "__init__.py").is_file()
    assert root.name == "opensre"


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/native_one_task.yml"


def _openrouter_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/openrouter_smoke_one_task.yml"


def _nvidia_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/nvidia_smoke_one_task.yml"


def test_launcher_rejects_task_globs() -> None:
    with pytest.raises(ValueError, match="exact published task name"):
        _validate_exact_task_name("*")


def test_harbor_command_structurally_limits_run_to_one_task(tmp_path: Path) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_config_path(),
        task_name="0123456789abcdef",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--agent") + 1] == AGENT_IMPORT_PATH
    assert command[command.index("--dataset") + 1] == DEFAULT_DATASET
    assert command[command.index("--include-task-name") + 1] == "0123456789abcdef"
    assert command[command.index("--n-tasks") + 1] == "1"
    assert command[command.index("--n-concurrent") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    assert "OPENAI_API_KEY=${OPENAI_API_KEY}" in command
    assert "OPENAI_BASE_URL=${OPENAI_BASE_URL}" in command
    assert "--disable-verification" not in command
    assert all(not argument.startswith("OPENAI_API_KEY=sk-") for argument in command)


def test_openrouter_smoke_command_uses_its_key_and_disables_verification(
    tmp_path: Path,
) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_openrouter_config_path(),
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "openrouter/openrouter/free"
    assert "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}" in command
    assert "--disable-verification" in command
    assert "--verifier-env" not in command
    assert all("OPENAI_" not in argument for argument in command)


def test_nvidia_smoke_command_forwards_only_its_provider_key(tmp_path: Path) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_nvidia_config_path(),
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "nvidia/z-ai/glm-5.2"
    assert "NVIDIA_API_KEY=${NVIDIA_API_KEY}" in command
    assert "--disable-verification" in command
    assert all("OPENROUTER_" not in argument for argument in command)
