from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.orcabench.config import BenchmarkSettings
from tests.benchmarks.orcabench.host.launcher import (
    AGENT_IMPORT_PATH,
    DEFAULT_DATASET,
    _opensre_repo_root,
    _parser,
    _validate_exact_task_name,
    build_harbor_command,
)


def test_opensre_repo_root_contains_importable_tests_package() -> None:
    root = _opensre_repo_root()

    assert (root / "tests" / "__init__.py").is_file()
    assert root.name == "opensre"


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/native_one_task.yml"


def _smoke_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs/smoke_one_task.yml"


def _settings(path: Path) -> BenchmarkSettings:
    return BenchmarkSettings.from_yaml(path)


def test_launcher_rejects_task_globs() -> None:
    with pytest.raises(ValueError, match="exact published task name"):
        _validate_exact_task_name("*")


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("gemini", "gemini-3.5-flash-lite"),
        ("groq", "llama-3.3-70b-versatile"),
    ],
)
def test_launcher_accepts_runtime_provider_and_model(
    provider: str,
    model: str,
) -> None:
    args = _parser().parse_args(
        [
            "--orca-repo",
            "/orca",
            "--bundle",
            "/bundle",
            "--task-name",
            "orca-bench/5b71925cf2820c86",
            "--provider",
            provider,
            "--model",
            model,
        ]
    )

    assert args.provider == provider
    assert args.model == model


def test_harbor_command_structurally_limits_run_to_one_task(tmp_path: Path) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_config_path(),
        settings=_settings(_config_path()),
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
    assert "model_provider=openai" in command
    assert all(not argument.startswith("OPENAI_API_KEY=sk-") for argument in command)


def test_openrouter_smoke_command_uses_its_key_and_disables_verification(
    tmp_path: Path,
) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_smoke_config_path(),
        settings=_settings(_smoke_config_path()).with_model_override(
            "openrouter",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ),
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert (
        command[command.index("--model") + 1]
        == "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    )
    assert "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}" in command
    assert "--disable-verification" in command
    assert "--verifier-env" not in command
    assert all("OPENAI_" not in argument for argument in command)


def test_nvidia_smoke_command_forwards_only_its_provider_key(tmp_path: Path) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_smoke_config_path(),
        settings=_settings(_smoke_config_path()).with_model_override(
            "nvidia",
            "z-ai/glm-5.2",
        ),
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "nvidia/z-ai/glm-5.2"
    assert "NVIDIA_API_KEY=${NVIDIA_API_KEY}" in command
    assert "--disable-verification" in command
    assert all("OPENROUTER_" not in argument for argument in command)


def test_groq_smoke_command_forwards_only_groq_key(tmp_path: Path) -> None:
    settings = _settings(_smoke_config_path()).with_model_override(
        "groq",
        "llama-3.3-70b-versatile",
    )
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_smoke_config_path(),
        settings=settings,
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "groq/llama-3.3-70b-versatile"
    assert "GROQ_API_KEY=${GROQ_API_KEY}" in command
    assert "model_provider=groq" in command
    assert "--disable-verification" in command
    assert all("OPENROUTER_" not in argument for argument in command)


def test_gemini_smoke_command_forwards_only_gemini_key(tmp_path: Path) -> None:
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_smoke_config_path(),
        settings=_settings(_smoke_config_path()),
        task_name="orca-bench/5b71925cf2820c86",
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "gemini/gemini-3.5-flash-lite"
    assert "GEMINI_API_KEY=${GEMINI_API_KEY}" in command
    assert "model_provider=gemini" in command
    assert "--disable-verification" in command
    assert all("GROQ_" not in argument for argument in command)
