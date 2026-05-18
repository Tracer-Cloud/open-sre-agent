from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "opensre_sync_release_version",
    _REPO_ROOT / "packaging" / "sync_release_version.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_normalize_release_version = _MODULE._normalize_release_version


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("v2026.4.13", "2026.4.13"),
        ("2026.4.13", "2026.4.13"),
        ("v0.1", "0.1"),
        ("0.1.0", "0.1.0"),
    ],
)
def test_normalize_release_version_accepts_calendar_and_semver(
    raw_value: str,
    expected: str,
) -> None:
    assert _normalize_release_version(raw_value) == expected


def test_normalize_release_version_rejects_unknown_shapes() -> None:
    with pytest.raises(ValueError, match="Release tag must look like"):
        _normalize_release_version("not-a-version")
