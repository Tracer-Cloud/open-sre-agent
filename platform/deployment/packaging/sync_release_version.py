"""Set ``pyproject.toml`` version before release builds."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_VERSION_LINE = re.compile(r'(?m)^version = "[^"]+"')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tag", help="Release tag, e.g. v0.1.2026.6.26")
    group.add_argument("--version", help="Explicit version, e.g. 0.1.2026.6.26+main.abc1234")
    args = parser.parse_args()

    version = (args.version or args.tag).strip().removeprefix("v")
    pyproject = ROOT / "pyproject.toml"
    updated, count = _VERSION_LINE.subn(
        f'version = "{version}"',
        pyproject.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Could not update version in {pyproject}")

    pyproject.write_text(updated, encoding="utf-8")
    print(f"Set version to {version}")


if __name__ == "__main__":
    main()
