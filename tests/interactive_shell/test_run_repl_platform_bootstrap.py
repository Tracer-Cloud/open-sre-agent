"""REPL entrypoints re-establish the first-party ``platform`` package.

Poison / re-cache scenarios run in a fresh interpreter. Healing replaces
``sys.modules['platform*']`` and would orphan imports already bound by other
test modules in this process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_in_fresh_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO_ROOT),
    )


def test_run_repl_async_bootstraps_before_platform_analytics_import() -> None:
    result = _run_in_fresh_interpreter(
        """
        import asyncio, sys, types
        from rich.console import Console
        from config.platform_bootstrap import ensure_project_platform_package
        from config.repl_config import ReplConfig
        import surfaces.interactive_shell.main as main
        import surfaces.interactive_shell.runtime.context as context

        class _Reached(Exception):
            pass

        ensure_project_platform_package()
        cfg = ReplConfig.load()
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]

        def _raise_reached(**_k):
            raise _Reached

        # Patched where ``run_repl_async`` imports them (call-time), not on ``main``.
        import surfaces.interactive_shell.ui.input_prompt as input_prompt

        input_prompt.build_prompt_session = lambda: object()
        context.create_repl_runtime_context = _raise_reached
        try:
            asyncio.run(main.run_repl_async(config=cfg, console=Console(force_terminal=False)))
        except _Reached:
            pass
        else:
            raise SystemExit("expected _Reached")
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_run_repl_bootstraps_platform_before_any_work() -> None:
    result = _run_in_fresh_interpreter(
        """
        import sys, types
        from rich.console import Console
        from config.platform_bootstrap import ensure_project_platform_package
        from config.repl_config import ReplConfig
        import surfaces.interactive_shell.main as main

        ensure_project_platform_package()
        cfg = ReplConfig.load()
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        rc = main.run_repl(config=cfg, console=Console(force_terminal=False))
        assert rc == 0
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_submodule_import_after_platform_recache_survives() -> None:
    result = _run_in_fresh_interpreter(
        """
        from config.platform_bootstrap import ensure_project_platform_package
        ensure_project_platform_package()
        import surfaces.interactive_shell.runtime
        import sys, types
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        import surfaces.interactive_shell.runtime.turn_host as th
        assert hasattr(th, "run_agent_turn")
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_runtime_package_import_survives_stdlib_platform_cached_first() -> None:
    """The runtime package re-exports ``platform.scheduling`` symbols as its API.

    Its module-level ``platform.*`` imports must heal a stdlib re-cache on import.
    ``main`` is an entrypoint with no module-level ``platform`` imports; its
    healing contract is covered by the ``run_repl*`` tests above instead.
    """
    result = _run_in_fresh_interpreter(
        """
        import sys, types
        sys.modules["platform"] = types.ModuleType("platform")
        __import__("surfaces.interactive_shell.runtime")
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_facade_run_repl_import_survives_stdlib_platform_cached_first() -> None:
    result = _run_in_fresh_interpreter(
        """
        import sys, types
        sys.modules["platform"] = types.ModuleType("platform")
        import surfaces.interactive_shell as shell
        sys.modules["platform"] = types.ModuleType("platform")
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]
        assert not hasattr(sys.modules["platform"], "__path__")
        rc = shell.run_repl()
        assert hasattr(sys.modules["platform"], "__path__")
        assert rc == 0
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
