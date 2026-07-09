"""Guard: the harness boot path imports without POSIX-only stdlib modules.

Frozen Windows builds have no ``resource`` / ``fcntl`` / ``termios`` / ``pwd`` /
``grp``. A bare top-level import of one anywhere the startup import chain reaches
crashes ``opensre.exe`` before it starts. This reproduces the Windows condition
in a subprocess (the modules are made unimportable) and asserts the boot chain
run by ``__main__`` still imports and installs cleanly.

Linux/macOS CI shards cannot catch this on their own — the modules are present
there — so this test simulates their absence.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# POSIX-only stdlib modules absent on Windows.
_POSIX_ONLY_MODULES = ("resource", "fcntl", "termios", "pwd", "grp")

# The exact boot chain __main__ runs at startup (see surfaces .../boundary.py).
_BOOT_CALL = (
    "from surfaces.interactive_shell.ui.output.boundary import install_harness_ports\n"
    "install_harness_ports()"
)


def test_harness_boot_imports_without_posix_only_modules() -> None:
    script = textwrap.dedent(
        """
        import builtins

        _blocked = set({blocked!r})
        _real_import = builtins.__import__

        def _guard(name, *args, **kwargs):
            if name.split(".", 1)[0] in _blocked:
                raise ModuleNotFoundError(f"No module named {{name!r}} (simulated Windows)")
            return _real_import(name, *args, **kwargs)

        builtins.__import__ = _guard
        {boot_call}
        print("BOOT_OK")
        """
    ).format(blocked=list(_POSIX_ONLY_MODULES), boot_call=_BOOT_CALL)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert "BOOT_OK" in result.stdout, (
        "harness boot failed to import with POSIX-only modules "
        f"{_POSIX_ONLY_MODULES} absent (Windows):\n{result.stderr}"
    )
    assert result.returncode == 0, result.stderr
