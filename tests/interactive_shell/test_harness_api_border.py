"""``surfaces/`` imports the agent harness only through its API.

Twin of ``gateway/tests/test_harness_api_border.py``. The allowlist is compared
exactly in both directions, so it can only shrink: a new internal import fails
immediately, and an entry no longer imported must be removed.
"""

from __future__ import annotations

from pathlib import Path

from tests.shared.harness_api import (
    assert_internal_imports_match_allowlist,
    internal_harness_imports_under,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal harness modules surfaces/ still imports directly. Empty: every
#: harness name surfaces/ uses comes through the API.
_ALLOWED_INTERNAL_IMPORTS: frozenset[str] = frozenset()


def test_surfaces_import_the_harness_only_through_its_api() -> None:
    imported = internal_harness_imports_under(REPO_ROOT / "surfaces")
    assert_internal_imports_match_allowlist(
        imported, _ALLOWED_INTERNAL_IMPORTS, package="surfaces/"
    )
