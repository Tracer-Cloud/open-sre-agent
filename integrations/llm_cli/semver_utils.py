"""Shared semver parsing/comparison helpers for CLI adapters."""

from __future__ import annotations

import re

_SEMVER_THREE_PART_RE = re.compile(r"(\d+\.\d+\.\d+)")


def parse_semver_three_part(text: str) -> str | None:
    """Extract ``major.minor.patch`` from arbitrary version output text."""
    match = _SEMVER_THREE_PART_RE.search(text or "")
    return match.group(1) if match else None


def semver_to_tuple(version: str) -> tuple[int, int, int]:
    """Convert a semver-ish string into a comparable (major, minor, patch) tuple."""
    parts = [int(m) for m in re.findall(r"\d+", version)][:3]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]
