"""CLI process startup: one named step, in a fixed order.

``main()`` opened with eight statements of process setup — env bootstrap, error
reporting, observability adapters, terminal prompt behaviour, signal handling —
before invoking the Click group. None of it is CLI-argument logic, and the
ordering constraints survived only as position in a long function.

Order is load-bearing and is what these tests pin:

* env variables load **before** Sentry, so the DSN and the no-telemetry opt-out
  are visible when error reporting initialises;
* observability adapters install **before** the group runs, so core code that
  calls the abstractions during a command routes through the Rich-aware
  implementations rather than the no-op defaults.

Importing this module must also stay cheap — see the fast-version test below.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from surfaces.cli.app import cli

# Env is bound via bootstrap.process; Sentry/adapters are imported
# inside CLI ``run()`` so those patches target the defining module.
_ENV = "bootstrap.process.bootstrap_opensre_env_once"
_SENTRY = "platform.observability.errors.sentry.init_sentry"
_ADAPTERS = "surfaces.interactive_shell.ui.output.boundary.install_product_adapters"
_ESCAPE = "platform.terminal.prompt_support.install_questionary_escape_cancel"
_CTRL_C = "platform.terminal.prompt_support.install_questionary_ctrl_c_double_exit"
_SIGINT = "surfaces.cli.signals.install_sigint_handler"


def _raise_missing_sentry(**_kwargs: Any) -> None:
    raise ModuleNotFoundError(name="sentry_sdk")


def _silence_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize every side effect so a test can assert one thing."""
    for target in (_ENV, _ADAPTERS, _ESCAPE, _CTRL_C, _SIGINT):
        monkeypatch.setattr(target, lambda *_a, **_kw: None)


def test_startup_runs_the_steps_in_the_required_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    order: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: order.append("env"))
    monkeypatch.setattr(_SENTRY, lambda **_kw: order.append("sentry"))
    monkeypatch.setattr(_ADAPTERS, lambda: order.append("adapters"))
    monkeypatch.setattr(_ESCAPE, lambda: None)
    monkeypatch.setattr(_CTRL_C, lambda: None)
    monkeypatch.setattr(_SIGINT, lambda: None)

    # Act
    startup.run(cli, ["doctor"])

    # Assert — shared bootstrap.process boots env; CLI owns Sentry then Rich adapters.
    assert order.index("env") < order.index("sentry"), order
    assert order.index("sentry") < order.index("adapters"), order


def test_missing_sentry_module_is_tolerated_during_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """``opensre update`` must still run when sentry_sdk is being replaced."""
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(_SENTRY, _raise_missing_sentry)

    # Act / Assert — no exception for the update path.
    startup.run(cli, ["update"])


def test_missing_sentry_module_still_raises_for_other_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside `update`, a missing sentry_sdk is a real broken install."""
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(_SENTRY, _raise_missing_sentry)

    # Act / Assert
    with pytest.raises(ModuleNotFoundError):
        startup.run(cli, ["doctor"])


def test_importing_the_entry_point_does_not_load_the_terminal_stack() -> None:
    """``opensre --version`` must answer before questionary/prompt_toolkit load.

    The entry point imports this module at module scope, so anything imported
    here lands in front of the fast-version path — a broken or missing terminal
    dependency would then break ``--version``, which exists precisely to work
    when the full CLI does not. Run in a clean interpreter because the test
    session has already imported half the product.
    """
    # Arrange
    probe = (
        "import sys; import surfaces.cli.__main__; "
        "print(len([n for n in sys.modules "
        "if n.split('.')[0] in {'questionary', 'prompt_toolkit'}]))"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assert
    assert result.stdout.strip() == "0", result.stdout


def test_fast_version_answers_without_the_terminal_stack() -> None:
    """The fast path prints a version and still loads no terminal stack."""
    # Arrange
    probe = (
        "import sys; from surfaces.cli.__main__ import main; main(['--version']); "
        "print('TERMINAL_MODULES', len([n for n in sys.modules "
        "if n.split('.')[0] in {'questionary', 'prompt_toolkit'}]))"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assert
    assert "TERMINAL_MODULES 0" in result.stdout, result.stdout
