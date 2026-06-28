"""Tests for infra/ci/run_test_scope.py."""

from __future__ import annotations

import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

_CI_DIR = Path(__file__).resolve().parents[2] / "infra" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from run_test_scope import _resolve_base_ref


def test_resolve_base_ref_prefers_local_branch_over_same_named_tag() -> None:
    def fake_run(args: list[str], **_: object) -> CompletedProcess[str]:
        ref = args[4].removesuffix("^{commit}")
        return CompletedProcess(args=args, returncode=0 if ref == "refs/heads/main" else 1)

    with patch("run_test_scope.subprocess.run", side_effect=fake_run):
        assert _resolve_base_ref("main") == "refs/heads/main"


def test_resolve_base_ref_leaves_qualified_refs_unchanged() -> None:
    assert _resolve_base_ref("refs/remotes/origin/main") == "refs/remotes/origin/main"
