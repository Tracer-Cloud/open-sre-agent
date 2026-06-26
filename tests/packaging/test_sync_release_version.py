from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "opensre_sync_release_version",
    _REPO_ROOT / "infra" / "deployment" / "packaging" / "sync_release_version.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_normalize_release_version = _MODULE._normalize_release_version


def test_release_paths_resolve_from_moved_infra_location() -> None:
    assert _MODULE.ROOT == _REPO_ROOT
    assert _MODULE.PYPROJECT_PATH == _REPO_ROOT / "pyproject.toml"
    assert _MODULE.APP_CONSTANTS_OPENSRE_PATH == _REPO_ROOT / "config" / "constants" / "opensre.py"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("v0.1.2026.6.26", "0.1.2026.6.26"),
        ("0.1.2026.6.26", "0.1.2026.6.26"),
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


@pytest.mark.parametrize("raw_value", ["not-a-version", "v0.1.2026.99.99"])
def test_normalize_release_version_rejects_unknown_shapes(raw_value: str) -> None:
    with pytest.raises(ValueError, match="Release tag must look like"):
        _normalize_release_version(raw_value)
