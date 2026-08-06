"""Build a hashed OpenSRE wheel and offline dependency wheelhouse for ORCA."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from tests.benchmarks.orcabench.host.bundle import (
    MANIFEST_NAME,
    WHEELHOUSE_NAME,
    file_sha256,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(".bench-cache/orcabench/builds"),
    )
    return parser


def _run(repo: Path, *args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def _git_state(repo: Path) -> tuple[str, tuple[str, ...]]:
    commit = _run(repo, "git", "rev-parse", "HEAD", capture=True)
    porcelain = _run(repo, "git", "status", "--porcelain", capture=True)
    dirty_files = tuple(line[3:] for line in porcelain.splitlines() if len(line) >= 4)
    return commit, dirty_files


def build_bundle(repo: Path, output_root: Path) -> Path:
    """Build one immutable bundle directory and return it after validation."""
    repo = repo.resolve()
    commit, dirty_files = _git_state(repo)
    if dirty_files:
        raise RuntimeError(
            "OpenSRE checkout must be clean before building a benchmark bundle; "
            f"changed files: {list(dirty_files)}"
        )
    bundle = (repo / output_root / commit[:12]).resolve()
    if bundle.exists():
        raise FileExistsError(
            f"bundle already exists: {bundle}. Reuse it or choose another output root."
        )

    wheelhouse = bundle / WHEELHOUSE_NAME
    wheelhouse.mkdir(parents=True)
    requirements = bundle / "requirements.txt"

    requirements.write_text(
        _run(
            repo,
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-editable",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            capture=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _run(repo, "uv", "build", "--wheel", "--out-dir", str(wheelhouse))
    _run(
        repo,
        "uv",
        "run",
        "--python",
        "3.13",
        "--with",
        "pip",
        "python",
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--dest",
        str(wheelhouse),
        "--requirement",
        str(requirements),
    )
    target_python = _run(
        repo,
        "uv",
        "run",
        "--python",
        "3.13",
        "python",
        "-c",
        "import platform; print(platform.python_version())",
        capture=True,
    )

    opensre_wheels = sorted(wheelhouse.glob("opensre-*.whl"))
    if len(opensre_wheels) != 1:
        raise RuntimeError(f"expected exactly one OpenSRE wheel, found {opensre_wheels}")

    members = sorted(path for path in bundle.rglob("*") if path.is_file())
    hashes = {str(path.relative_to(bundle)): file_sha256(path) for path in members}
    manifest = {
        "schema_version": 1,
        "opensre_commit": commit,
        "dirty_files": dirty_files,
        "python_version": target_python,
        "opensre_wheel": str(opensre_wheels[0].relative_to(bundle)),
        "files_sha256": hashes,
    }
    (bundle / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bundle


def main() -> int:
    """Build the bundle from the current OpenSRE checkout."""
    args = _parser().parse_args()
    repo = Path(__file__).resolve().parents[5]
    bundle = build_bundle(repo, args.output_root)
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
