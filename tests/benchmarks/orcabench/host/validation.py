"""Fast, read-only validation for the local OpenSRE/ORCA/Harbor setup."""

from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.benchmarks.orcabench.config import BenchmarkSettings
from tests.benchmarks.orcabench.host.bundle import validate_bundle


@dataclass(frozen=True)
class CheckResult:
    """One setup check and an actionable result."""

    name: str
    ok: bool
    detail: str


def _parser() -> argparse.ArgumentParser:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orca-repo", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=package_dir / "configs/native_one_task.yml",
    )
    return parser


def _docker_check() -> CheckResult:
    try:
        result = subprocess.run(
            ("docker", "version", "--format", "{{.Server.Version}}"),
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("docker", False, str(exc))
    return CheckResult("docker", True, f"server {result.stdout.strip()}")


def _harbor_check() -> CheckResult:
    try:
        from harbor.models.task.task import Task

        version = importlib.metadata.version("harbor")
        checksum_getter = Task.checksum.fget
        if checksum_getter is None:
            raise RuntimeError("Task.checksum property has no getter")
        source = inspect.getsource(checksum_getter)
    except Exception as exc:
        return CheckResult("harbor", False, str(exc))
    if version != "0.20.0":
        return CheckResult("harbor", False, f"expected 0.20.0, found {version}")
    if ".trials/" not in source or "ignore" not in source:
        return CheckResult(
            "harbor",
            False,
            "Task.checksum is not patched to ignore environment/.trials/",
        )
    return CheckResult("harbor", True, "0.20.0 with ORCA checksum patch")


def validate(orca_repo: Path, config: Path, bundle: Path | None) -> list[CheckResult]:
    """Run bounded local checks without pulling, building, or starting a task."""
    orca = orca_repo.expanduser().resolve()
    results = [
        CheckResult("orca_repo", (orca / "job-config.yaml").is_file(), str(orca)),
        CheckResult(
            "orca_snapshot_patch",
            (orca / "patches/harbor_models_task_task.py").is_file(),
            str(orca / "patches/harbor_models_task_task.py"),
        ),
    ]
    try:
        settings = BenchmarkSettings.from_yaml(config.expanduser().resolve())
        results.append(CheckResult("benchmark_config", True, settings.model.harbor_model))
    except Exception as exc:
        results.append(CheckResult("benchmark_config", False, str(exc)))

    if bundle is not None:
        try:
            manifest = validate_bundle(bundle)
            results.append(CheckResult("offline_bundle", True, manifest.opensre_commit))
        except Exception as exc:
            results.append(CheckResult("offline_bundle", False, str(exc)))
    results.extend((_docker_check(), _harbor_check()))
    return results


def main() -> int:
    """Print a compact setup report and fail if any prerequisite is missing."""
    args = _parser().parse_args()
    results = validate(args.orca_repo, args.config, args.bundle)
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
