#!/usr/bin/env python3
"""Sync release metadata before building distributions."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_CONSTANTS = ROOT / "config" / "constants" / "opensre.py"


def _version_from_tag(tag: str) -> str:
    version = tag.removeprefix("v").strip()
    if not version:
        raise ValueError("release tag must contain a version")
    return version


def _replace_once(text: str, pattern: str, replacement: str, *, path: Path) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"failed to update version in {path}")
    return updated


def sync_release_version(tag: str) -> str:
    """Update version declarations used by wheels and frozen binaries."""
    version = _version_from_tag(tag)

    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    pyproject_text = _replace_once(
        pyproject_text,
        r'(?m)^version = "[^"]+"',
        f'version = "{version}"',
        path=PYPROJECT,
    )
    PYPROJECT.write_text(pyproject_text, encoding="utf-8")

    constants_text = VERSION_CONSTANTS.read_text(encoding="utf-8")
    constants_text = _replace_once(
        constants_text,
        r'(?m)^DEFAULT_RELEASE_VERSION: Final\[str\] = "[^"]+"',
        f'DEFAULT_RELEASE_VERSION: Final[str] = "{version}"',
        path=VERSION_CONSTANTS,
    )
    VERSION_CONSTANTS.write_text(constants_text, encoding="utf-8")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v0.1.2026.6.26 or v0.1")
    args = parser.parse_args()
    version = sync_release_version(args.tag)
    print(f"Synced release version to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
