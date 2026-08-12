"""Stage ORCA data and launch selected native OpenSRE Harbor tasks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from tests.benchmarks.orcabench.config import (
    BENCHMARK_PROVIDER_VALUES,
    BenchmarkSettings,
)
from tests.benchmarks.orcabench.host.bundle import validate_bundle
from tests.benchmarks.orcabench.host.snapshot import stage_snapshot

DEFAULT_SNAPSHOT_IMAGE = "orcabench/sre-otel-snapshot:data-0418-harbor-template"
DEFAULT_DATASET = "orca-bench/orca-bench@latest"
AGENT_IMPORT_PATH = "tests.benchmarks.orcabench.host.agent:OpenSRENativeAgent"


def _opensre_repo_root() -> Path:
    """Return the import root containing the top-level ``tests`` package."""
    return Path(__file__).resolve().parents[4]


def _parser() -> argparse.ArgumentParser:
    package_dir = Path(__file__).resolve().parents[1]
    opensre_repo = package_dir.parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orca-repo", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--task-name",
        action="append",
        required=True,
        help="Exact published task name; repeat to run selected tasks in one job.",
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument(
        "--config",
        type=Path,
        default=package_dir / "configs/native_one_task.yml",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=opensre_repo / ".bench-cache/orcabench/snapshots",
    )
    parser.add_argument("--snapshot-image", default=DEFAULT_SNAPSHOT_IMAGE)
    parser.add_argument("--provider", choices=BENCHMARK_PROVIDER_VALUES)
    parser.add_argument(
        "--model",
        help="Provider-native model ID; requires --provider",
    )
    parser.add_argument("--print-command", action="store_true")
    return parser


def _validate_exact_task_name(task_name: str) -> str:
    value = task_name.strip()
    if not value or any(character in value for character in "*?["):
        raise ValueError("--task-name must be one exact published task name, not a glob")
    return value


def _validate_exact_task_names(task_names: list[str]) -> tuple[str, ...]:
    """Validate a non-empty, duplicate-free selection of exact task names."""
    validated = tuple(_validate_exact_task_name(task_name) for task_name in task_names)
    if len(set(validated)) != len(validated):
        raise ValueError("--task-name values must not contain duplicates")
    return validated


def _environment_flags(flag: str, names: tuple[str, ...]) -> list[str]:
    """Build Harbor's deferred, secret-safe environment arguments."""
    return [item for name in names for item in (flag, f"{name}=${{{name}}}")]


def build_harbor_command(
    *,
    orca_repo: Path,
    bundle: Path,
    config_path: Path,
    settings: BenchmarkSettings,
    task_names: tuple[str, ...],
    snapshot_cache: Path,
    dataset: str = DEFAULT_DATASET,
) -> tuple[str, ...]:
    """Build the selected-task Harbor command with secret-safe env templates."""
    job_config = orca_repo / "job-config.yaml"
    mounts = json.dumps(
        [
            {
                "type": "bind",
                "source": str(snapshot_cache),
                "target": str(snapshot_cache),
                "read_only": True,
            }
        ],
        separators=(",", ":"),
    )
    command = [
        "uv",
        "run",
        "harbor",
        "run",
        "-c",
        str(job_config),
        "--agent",
        AGENT_IMPORT_PATH,
        "--model",
        settings.model.harbor_model,
        "--agent-kwarg",
        f"benchmark_config_path={config_path}",
        "--agent-kwarg",
        f"bundle_path={bundle}",
        "--agent-kwarg",
        f"model_provider={settings.model.provider}",
    ]
    command.extend(
        _environment_flags("--agent-env", settings.model.required_environment_names)
    )
    if settings.verifier.enabled:
        command.extend(
            _environment_flags(
                "--verifier-env", settings.verifier.required_environment_names
            )
        )
    else:
        command.append("--disable-verification")
    command.extend(["--dataset", dataset])
    for task_name in task_names:
        command.extend(["--include-task-name", task_name])
    command.extend(
        [
            "--n-tasks",
            str(len(task_names)),
            "--n-concurrent",
            "1",
            "--max-retries",
            "0",
            "--agent-include-logs",
            "opensre-orca/**",
            "--mounts-json",
            mounts,
        ]
    )
    return tuple(command)


def run_tasks(args: argparse.Namespace) -> int:
    """Validate inputs, stage one snapshot cache, and execute one Harbor job."""
    orca_repo = args.orca_repo.expanduser().resolve()
    bundle = args.bundle.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    task_names = _validate_exact_task_names(args.task_name)

    if not (orca_repo / "job-config.yaml").is_file():
        raise FileNotFoundError(f"ORCA job config is missing: {orca_repo / 'job-config.yaml'}")
    validate_bundle(bundle)
    settings = BenchmarkSettings.from_yaml(config_path).with_model_override(
        args.provider,
        args.model,
    )
    required_names = dict.fromkeys(
        settings.model.required_environment_names
        + settings.verifier.required_environment_names
    )
    for required in required_names:
        if not os.environ.get(required, "").strip():
            raise RuntimeError(f"{required} must be set in the host environment")

    snapshot_cache = stage_snapshot(args.snapshot_image, args.cache_root)
    command = build_harbor_command(
        orca_repo=orca_repo,
        bundle=bundle,
        config_path=config_path,
        settings=settings,
        task_names=task_names,
        snapshot_cache=snapshot_cache,
        dataset=args.dataset,
    )
    if args.print_command:
        print(subprocess.list2cmdline(command))

    opensre_repo = _opensre_repo_root()
    environment = dict(os.environ)
    python_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(opensre_repo), python_path) if value
    )
    environment["SNAPSHOT_CACHE_HOST_DIR"] = str(snapshot_cache)
    result = subprocess.run(command, cwd=orca_repo, env=environment, check=False)
    return result.returncode


def main() -> int:
    """CLI entry point."""
    return run_tasks(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
