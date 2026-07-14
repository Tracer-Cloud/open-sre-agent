"""Guards against CI collecting zero tests under pytest-xdist.

Two failure modes have produced ``N workers [0 items]`` in CI:

1. A mangled ``PYTEST_MARKER_EXPR`` (e.g. boolean ``false``) deselects everything.
   ``tests/conftest.py`` forces exit code 5 in that case.

2. A missing path argument (file/dir deleted but still listed in
   ``.github/workflows/ci.yml``) makes xdist abort collection for the *whole*
   shard — even when other paths are valid. Seen after ``tests/github`` was
   removed while ``cli-runtime`` still referenced it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PATH_RE = re.compile(r"^(tests/\S+|gateway/tests)$")


def test_xdist_empty_marker_exits_no_tests_collected() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "2",
            "-q",
            "tests/packaging",
            "-m",
            "false",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "0 items" in result.stdout or "0 items" in result.stderr
    assert result.returncode == 5, (
        f"expected ExitCode.NO_TESTS_COLLECTED (5), got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_ci_pytest_paths_exist_in_git_tree() -> None:
    """Every ``matrix.pytest_paths`` entry must exist in the committed tree.

    Local empty leftover dirs (e.g. ``tests/github/`` with only ``__pycache__``)
    hide this; CI checkouts do not have them, and a missing path zeros xdist.
    """
    tracked = set(
        subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "ls-tree", "-r", "--name-only", "HEAD"],
            text=True,
        ).splitlines()
    )

    def _present(path: str) -> bool:
        if path in tracked:
            return True
        prefix = path.rstrip("/") + "/"
        return any(entry.startswith(prefix) for entry in tracked)

    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    missing: list[str] = []
    for job in workflow.get("jobs", {}).values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for entry in matrix.get("include") or []:
            raw = entry.get("pytest_paths")
            if not raw:
                continue
            shard = entry.get("shard", "?")
            for token in str(raw).split():
                if not _PATH_RE.match(token):
                    continue
                if not _present(token):
                    missing.append(f"{shard}: {token}")

    assert not missing, (
        "CI pytest_paths missing from git tree (xdist will collect 0 items):\n"
        + "\n".join(f"  - {item}" for item in missing)
    )
