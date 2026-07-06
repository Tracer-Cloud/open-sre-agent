"""Set ``pyproject.toml`` version before release builds."""

from __future__ import annotations

import argparse
import re

from config.constants.paths import REPO_ROOT

_VERSION_LINE = re.compile(r'(?m)^version = "[^"]+"')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tag", help="Release tag, e.g. v0.1.2026.6.26")
    group.add_argument("--version", help="Explicit version, e.g. 0.1.2026.6.26+main.abc1234")
    args = parser.parse_args()

    version = (args.version or args.tag).strip().removeprefix("v")
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    updated, count = _VERSION_LINE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        msg = f"Could not update version in {REPO_ROOT / 'pyproject.toml'}"
        raise RuntimeError(msg)

    (REPO_ROOT / "pyproject.toml").write_text(updated, encoding="utf-8")
    print(f"Set version to {version}")


if __name__ == "__main__":
    main()
