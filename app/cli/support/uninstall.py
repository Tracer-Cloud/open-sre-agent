from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from app.constants import LEGACY_TRACER_HOME_DIR, OPENSRE_HOME_DIR
from app.version import PACKAGE_NAME


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _remove_path(p: Path) -> tuple[bool, str | None]:
    if not p.exists():
        return True, None
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True, None
    except OSError as exc:
        return False, str(exc)


def _pip_uninstall() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "--yes", PACKAGE_NAME],
        check=False,
        capture_output=True,
    )
    return result.returncode


def _data_dirs() -> list[Path]:
    return [OPENSRE_HOME_DIR, LEGACY_TRACER_HOME_DIR]


def run_uninstall(*, yes: bool = False) -> int:
    dirs = _data_dirs()
    binary = _is_binary_install()
    exe_path = Path(sys.executable)

    print()
    print("  The following will be permanently deleted:")
    print()
    for d in dirs:
        tag = "found" if d.exists() else "not found"
        print(f"    {d}  ({tag})")
    if binary:
        print(f"    {exe_path}  (binary)")
    else:
        print(f"    pip package: {PACKAGE_NAME}")
    print()

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(
                "  Uninstall opensre from this machine?", default=False
            ).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    print()

    any_error = False

    for d in dirs:
        if not d.exists():
            print(f"  skipped  {d}  (not found)")
            continue
        ok, err = _remove_path(d)
        if ok:
            print(f"  deleted  {d}")
        else:
            print(f"  error    {d}: {err}", file=sys.stderr)
            any_error = True

    if binary:
        ok, err = _remove_path(exe_path)
        if ok:
            print(f"  deleted  {exe_path}")
        else:
            print(f"  error    {exe_path}: {err}", file=sys.stderr)
            any_error = True
    else:
        print(f"  running  pip uninstall {PACKAGE_NAME}")
        rc = _pip_uninstall()
        if rc == 0:
            print(f"  deleted  pip package {PACKAGE_NAME}")
        else:
            print(f"  error    pip uninstall failed (exit {rc})", file=sys.stderr)
            if _is_windows():
                hint = f"pip uninstall {PACKAGE_NAME}"
            else:
                hint = f"pip uninstall {PACKAGE_NAME}  (or: pipx uninstall {PACKAGE_NAME})"
            print(f"           retry manually: {hint}", file=sys.stderr)
            any_error = True

    print()

    if any_error:
        print("  Uninstall finished with errors. See above for details.", file=sys.stderr)
        return 1

    print("  opensre has been uninstalled.")
    print()
    print("  Your config and data have been removed.")
    print("  To reinstall: curl -fsSL https://install.opensre.com | bash")
    return 0
