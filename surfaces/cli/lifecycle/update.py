from __future__ import annotations

import subprocess
import sys

from config.version import get_opensre_version
from infrastructure.process.release_version import (
    MAIN_BUILD_RELEASE_URL,
    fetch_latest_version,
    is_editable_install,
    is_update_available,
)

_INSTALL_SCRIPT = "https://install.opensre.com"
_INSTALL_SCRIPT_PS1 = "https://install.opensre.com"


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return sys.platform == "win32"


def _upgrade_via_install_script() -> int:
    """Download and run the official install script on the rolling main channel."""
    if _is_windows():
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$env:OPENSRE_INSTALL_CHANNEL='main'; "
                    f"Remove-Item Env:OPENSRE_VERSION -ErrorAction SilentlyContinue; "
                    f"irm {_INSTALL_SCRIPT_PS1} | iex"
                ),
            ],
            check=False,
        )
    else:
        result = subprocess.run(
            ["bash", "-c", f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"],
            check=False,
        )
    return result.returncode


def run_update(*, check_only: bool = False, yes: bool = False) -> int:
    # To skip this check in CI or automated environments, set OPENSRE_NO_UPDATE_CHECK=1.
    current = get_opensre_version()

    try:
        latest = fetch_latest_version()
    except Exception as exc:
        print(f"  error: could not fetch latest version: {exc}", file=sys.stderr)
        return 1

    if not latest:
        print(
            "  error: could not determine latest main build version from release data.",
            file=sys.stderr,
        )
        return 1

    if not is_update_available(current, latest):
        print(f"  opensre {current} is already up to date.")
        return 0

    print(f"  current: {current}")
    print(f"  latest:  {latest}")
    print("  main build: " + MAIN_BUILD_RELEASE_URL)

    if check_only:
        return 1

    if is_editable_install():
        print(
            "  warning: this is an editable install — upgrading will replace it with a main build."
        )

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(f"  Update to main build {latest}?", default=True).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    rc = _upgrade_via_install_script()
    if rc == 0:
        print(f"  updated: {current} -> {latest}")
        print("  main build release: " + MAIN_BUILD_RELEASE_URL)
    else:
        print(f"  install script failed (exit {rc}).", file=sys.stderr)
        if _is_windows():
            hint = f"$env:OPENSRE_INSTALL_CHANNEL='main'; irm {_INSTALL_SCRIPT_PS1} | iex"
        else:
            hint = f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"
        print(f"  to retry manually, run:\n    {hint}", file=sys.stderr)
    return rc
