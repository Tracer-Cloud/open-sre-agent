"""Guard: empty xdist collections must fail CI (not exit 0).

When ``-m`` deselects everything (historically a mangled ``PYTEST_MARKER_EXPR``
that became the boolean/string ``false``), pytest-xdist prints
``N workers [0 items]`` and can still exit 0 on large path sets. The root
``tests/conftest.py`` ``pytest_sessionfinish`` hook forces
``ExitCode.NO_TESTS_COLLECTED`` (5) so CI cannot go green while running nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
