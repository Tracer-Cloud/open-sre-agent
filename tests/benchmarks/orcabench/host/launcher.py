"""Stage ORCA data and launch exactly one native OpenSRE Harbor task."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from tests.benchmarks.orcabench.config import BenchmarkSettings
from tests.benchmarks.orcabench.host.bundle import validate_bundle
from tests.benchmarks.orcabench.host.snapshot import stage_snapshot

DEFAULT_SNAPSHOT_IMAGE = "orcabench/sre-otel-snapshot:data-0418-harbor-template"
AGENT_IMPORT_PATH = "tests.benchmarks.orcabench.host.agent:OpenSRENativeAgent"


def _parser() -> argparse.ArgumentParser:
    package_dir = Path(__file__).resolve().parents[1]
    opensre_repo = package_dir.parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orca-repo", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--task-name", required=True)
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
    parser.add_argument("--print-command", action="store_true")
    return parser


def _validate_exact_task_name(task_name: str) -> str:
    value = task_name.strip()
    if not value or any(character in value for character in "*?["):
        raise ValueError("--task-name must be one exact published task name, not a glob")
    return value


def build_harbor_command(
    *,
    orca_repo: Path,
    bundle: Path,
    config_path: Path,
    task_name: str,
    snapshot_cache: Path,
) -> tuple[str, ...]:
    """Build the single-task Harbor command with secret-safe env templates."""
    settings = BenchmarkSettings.from_yaml(config_path)
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
    return (
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
        "--agent-env",
        "OPENAI_API_KEY=${OPENAI_API_KEY}",
        "--agent-env",
        "OPENAI_BASE_URL=${OPENAI_BASE_URL}",
        "--verifier-env",
        "OPENAI_API_KEY=${OPENAI_API_KEY}",
        "--verifier-env",
        "OPENAI_BASE_URL=${OPENAI_BASE_URL}",
        "--include-task-name",
        task_name,
        "--n-tasks",
        "1",
        "--n-concurrent-trials",
        "1",
        "--max-retries",
        "0",
        "--agent-include-logs",
        "opensre-orca/**",
        "--mounts-json",
        mounts,
    )


def run_one(args: argparse.Namespace) -> int:
    """Validate all local inputs, stage the snapshot, and execute Harbor."""
    orca_repo = args.orca_repo.expanduser().resolve()
    bundle = args.bundle.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    task_name = _validate_exact_task_name(args.task_name)

    if not (orca_repo / "job-config.yaml").is_file():
        raise FileNotFoundError(f"ORCA job config is missing: {orca_repo / 'job-config.yaml'}")
    validate_bundle(bundle)
    BenchmarkSettings.from_yaml(config_path)
    for required in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if not os.environ.get(required, "").strip():
            raise RuntimeError(f"{required} must be set in the host environment")

    snapshot_cache = stage_snapshot(args.snapshot_image, args.cache_root)
    command = build_harbor_command(
        orca_repo=orca_repo,
        bundle=bundle,
        config_path=config_path,
        task_name=task_name,
        snapshot_cache=snapshot_cache,
    )
    if args.print_command:
        print(subprocess.list2cmdline(command))

    opensre_repo = Path(__file__).resolve().parents[3]
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
    return run_one(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
