from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.orcabench.config import BenchmarkSettings
from tests.benchmarks.orcabench.host import launcher
from tests.benchmarks.orcabench.host.launcher import (
    AGENT_IMPORT_PATH,
    DEFAULT_DATASET,
    _opensre_repo_root,
    _parser,
    _validate_exact_task_name,
    _validate_exact_task_names,
    build_harbor_command,
    run_tasks,
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


def test_launcher_rejects_duplicate_task_names() -> None:
    with pytest.raises(ValueError, match="must not contain duplicates"):
        _validate_exact_task_names(["orca-bench/a", "orca-bench/a"])


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
    assert args.task_name == ["orca-bench/5b71925cf2820c86"]


def test_launcher_accepts_repeated_exact_task_names() -> None:
    args = _parser().parse_args(
        [
            "--orca-repo",
            "/orca",
            "--bundle",
            "/bundle",
            "--task-name",
            "orca-bench/a",
            "--task-name",
            "orca-bench/b",
        ]
    )

    assert _validate_exact_task_names(args.task_name) == ("orca-bench/a", "orca-bench/b")


def test_launcher_uses_config_tool_capability_mode_by_default() -> None:
    args = _parser().parse_args(
        [
            "--orca-repo",
            "/orca",
            "--bundle",
            "/bundle",
            "--task-name",
            "orca-bench/a",
        ]
    )

    assert args.tool_capability_mode is None


def test_launcher_accepts_native_tool_capability_mode() -> None:
    args = _parser().parse_args(
        [
            "--orca-repo",
            "/orca",
            "--bundle",
            "/bundle",
            "--task-name",
            "orca-bench/a",
            "--tool-capability-mode",
            "native",
        ]
    )

    assert args.tool_capability_mode == "native"


def test_harbor_command_preserves_selected_task_names(tmp_path: Path) -> None:
    task_names = ("0123456789abcdef", "fedcba9876543210")
    command = build_harbor_command(
        orca_repo=tmp_path / "ORCA-bench",
        bundle=tmp_path / "bundle",
        config_path=_config_path(),
        settings=_settings(_config_path()),
        task_names=task_names,
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--agent") + 1] == AGENT_IMPORT_PATH
    assert command[command.index("--dataset") + 1] == DEFAULT_DATASET
    selected_task_names = tuple(
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--include-task-name"
    )
    assert selected_task_names == task_names
    assert command[command.index("--n-tasks") + 1] == "2"
    assert command[command.index("--n-concurrent") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    assert "OPENAI_API_KEY=${OPENAI_API_KEY}" in command
    assert "OPENAI_BASE_URL=${OPENAI_BASE_URL}" in command
    assert "--disable-verification" not in command
    assert "model_provider=openai" in command
    assert "tool_capability_mode=terminus_parity" in command
    assert all(not argument.startswith("OPENAI_API_KEY=sk-") for argument in command)


def test_launcher_stages_once_and_starts_one_job_for_selected_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    orca_repo = tmp_path / "ORCA-bench"
    orca_repo.mkdir()
    (orca_repo / "job-config.yaml").touch()
    bundle = tmp_path / "bundle"
    snapshot_cache = tmp_path / "snapshot"
    args = _parser().parse_args(
        [
            "--orca-repo",
            str(orca_repo),
            "--bundle",
            str(bundle),
            "--task-name",
            "orca-bench/a",
            "--task-name",
            "orca-bench/b",
            "--config",
            str(_smoke_config_path()),
        ]
    )
    stage_calls: list[tuple[str, Path]] = []
    subprocess_calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def validate_bundle(_: Path) -> None:
        """Avoid requiring a real bundle for launcher orchestration coverage."""

    def stage_snapshot(image: str, cache_root: Path) -> Path:
        """Record the single snapshot staging call and return a test cache path."""
        stage_calls.append((image, cache_root))
        return snapshot_cache

    def run_subprocess(
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
    ) -> object:
        """Capture the one Harbor invocation without starting a real job."""
        assert check is False
        subprocess_calls.append((command, cwd, env))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(launcher, "validate_bundle", validate_bundle)
    monkeypatch.setattr(launcher, "stage_snapshot", stage_snapshot)
    monkeypatch.setattr(launcher.subprocess, "run", run_subprocess)

    assert run_tasks(args) == 0
    assert len(stage_calls) == 1
    assert len(subprocess_calls) == 1
    command, cwd, environment = subprocess_calls[0]
    assert cwd == orca_repo
    assert environment["SNAPSHOT_CACHE_HOST_DIR"] == str(snapshot_cache)
    assert tuple(
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--include-task-name"
    ) == ("orca-bench/a", "orca-bench/b")
    assert "tool_capability_mode=terminus_parity" in command


def test_launcher_tool_capability_mode_flag_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    orca_repo = tmp_path / "ORCA-bench"
    orca_repo.mkdir()
    (orca_repo / "job-config.yaml").touch()
    bundle = tmp_path / "bundle"
    snapshot_cache = tmp_path / "snapshot"
    args = _parser().parse_args(
        [
            "--orca-repo",
            str(orca_repo),
            "--bundle",
            str(bundle),
            "--task-name",
            "orca-bench/a",
            "--config",
            str(_smoke_config_path()),
            "--tool-capability-mode",
            "native",
        ]
    )
    subprocess_calls: list[tuple[str, ...]] = []

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(launcher, "validate_bundle", lambda _: None)
    monkeypatch.setattr(launcher, "stage_snapshot", lambda *_: snapshot_cache)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, **_: subprocess_calls.append(command)
        or type("Result", (), {"returncode": 0})(),
    )

    assert run_tasks(args) == 0
    assert len(subprocess_calls) == 1
    assert "tool_capability_mode=native" in subprocess_calls[0]


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
        task_names=("orca-bench/5b71925cf2820c86",),
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
        task_names=("orca-bench/5b71925cf2820c86",),
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
        task_names=("orca-bench/5b71925cf2820c86",),
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
        task_names=("orca-bench/5b71925cf2820c86",),
        snapshot_cache=tmp_path / "snapshot",
    )

    assert command[command.index("--model") + 1] == "gemini/gemini-3.5-flash-lite"
    assert "GEMINI_API_KEY=${GEMINI_API_KEY}" in command
    assert "model_provider=gemini" in command
    assert "--disable-verification" in command
    assert all("GROQ_" not in argument for argument in command)
