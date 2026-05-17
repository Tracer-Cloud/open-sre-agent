"""Sync release-facing version strings to a Git tag-derived version."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"
APP_CONSTANTS_OPENSRE_PATH = ROOT / "app" / "constants" / "opensre.py"
CALENDAR_VERSION_PATTERN = re.compile(r"v?(?P<version>\d{4}\.\d{1,2}\.\d{1,2})")
SEMVER_VERSION_PATTERN = re.compile(r"v?(?P<version>\d+\.\d+(?:\.\d+)?)")


def _normalize_release_version(raw_value: str) -> str:
    value = raw_value.strip()
    for pattern in (CALENDAR_VERSION_PATTERN, SEMVER_VERSION_PATTERN):
        match = pattern.fullmatch(value)
        if match is not None:
            return match.group("version")

    msg = (
        "Release tag must look like 'vYYYY.M.D', 'YYYY.M.D', 'v0.1', or '0.1.0'; "
        f"got {raw_value!r}."
    )
    raise ValueError(msg)


def _replace_project_version(version: str, text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project_section = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            in_project_section = stripped == "[project]"
            continue

        if in_project_section and line.lstrip().startswith("version = "):
            lines[index] = re.sub(
                r'(?P<prefix>\bversion\s*=\s*")[^"]+(?P<suffix>")',
                rf"\g<prefix>{version}\g<suffix>",
                line,
                count=1,
            )
            return "".join(lines)

    msg = f"Could not find [project].version in {PYPROJECT_PATH}."
    raise RuntimeError(msg)


def _replace_default_release_version(version: str, text: str) -> str:
    lines = text.splitlines(keepends=True)

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if stripped.startswith('DEFAULT_RELEASE_VERSION: Final[str] = "'):
            line_ending = _line_ending_for(line)
            lines[index] = f'DEFAULT_RELEASE_VERSION: Final[str] = "{version}"{line_ending}'
            return "".join(lines)

    msg = f"Could not find DEFAULT_RELEASE_VERSION in {APP_CONSTANTS_OPENSRE_PATH}."
    raise RuntimeError(msg)


def _line_ending_for(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _sync_file(path: Path, updater: Callable[[str, str], str], version: str) -> None:
    original_text = path.read_text(encoding="utf-8")
    updated_text = updater(version, original_text)
    path.write_text(updated_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        required=True,
        help="Release tag to sync from, e.g. v2026.4.13 or v0.1.",
    )
    args = parser.parse_args()

    version = _normalize_release_version(args.tag)
    _sync_file(PYPROJECT_PATH, _replace_project_version, version)
    _sync_file(APP_CONSTANTS_OPENSRE_PATH, _replace_default_release_version, version)
    print(f"Synchronized release version to {version}")


if __name__ == "__main__":
    main()
