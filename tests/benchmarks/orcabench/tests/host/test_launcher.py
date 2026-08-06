from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.orcabench.host.launcher import (
    AGENT_IMPORT_PATH,
    _validate_exact_task_name,
    build_harbor_command,
)


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/native_one_task.yml"


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
    assert command[command.index("--include-task-name") + 1] == "0123456789abcdef"
    assert command[command.index("--n-tasks") + 1] == "1"
    assert command[command.index("--n-concurrent-trials") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    assert "OPENAI_API_KEY=${OPENAI_API_KEY}" in command
    assert all(not argument.startswith("OPENAI_API_KEY=sk-") for argument in command)
