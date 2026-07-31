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
"""

from __future__ import annotations

from typing import Any

from surfaces.cli.__main__ import cli


def test_startup_runs_the_steps_in_the_required_order(monkeypatch: Any) -> None:
    # Arrange
    from surfaces.cli import startup

    order: list[str] = []
    monkeypatch.setattr(startup, "bootstrap_opensre_env_once", lambda **_kw: order.append("env"))
    monkeypatch.setattr(startup, "init_sentry", lambda **_kw: order.append("sentry"))
    monkeypatch.setattr(startup, "install_product_adapters", lambda: order.append("adapters"))
    monkeypatch.setattr(startup, "install_questionary_escape_cancel", lambda: None)
    monkeypatch.setattr(startup, "install_questionary_ctrl_c_double_exit", lambda: None)
    monkeypatch.setattr(startup, "install_sigint_handler", lambda: None)

    # Act
    startup.run(cli, ["doctor"])

    # Assert
    assert order.index("env") < order.index("sentry"), order
    assert order.index("sentry") < order.index("adapters"), order


def test_missing_sentry_module_is_tolerated_during_update(monkeypatch: Any) -> None:
    """``opensre update`` must still run when sentry_sdk is being replaced."""
    # Arrange
    from surfaces.cli import startup

    def _raise_missing_sentry(**_kw: Any) -> None:
        raise ModuleNotFoundError(name="sentry_sdk")

    monkeypatch.setattr(startup, "bootstrap_opensre_env_once", lambda **_kw: None)
    monkeypatch.setattr(startup, "init_sentry", _raise_missing_sentry)
    monkeypatch.setattr(startup, "install_product_adapters", lambda: None)
    monkeypatch.setattr(startup, "install_questionary_escape_cancel", lambda: None)
    monkeypatch.setattr(startup, "install_questionary_ctrl_c_double_exit", lambda: None)
    monkeypatch.setattr(startup, "install_sigint_handler", lambda: None)

    # Act / Assert — no exception for the update path.
    startup.run(cli, ["update"])


def test_missing_sentry_module_still_raises_for_other_commands(monkeypatch: Any) -> None:
    """Outside `update`, a missing sentry_sdk is a real broken install."""
    import pytest

    from surfaces.cli import startup

    def _raise_missing_sentry(**_kw: Any) -> None:
        raise ModuleNotFoundError(name="sentry_sdk")

    monkeypatch.setattr(startup, "bootstrap_opensre_env_once", lambda **_kw: None)
    monkeypatch.setattr(startup, "init_sentry", _raise_missing_sentry)
    monkeypatch.setattr(startup, "install_product_adapters", lambda: None)
    monkeypatch.setattr(startup, "install_questionary_escape_cancel", lambda: None)
    monkeypatch.setattr(startup, "install_questionary_ctrl_c_double_exit", lambda: None)
    monkeypatch.setattr(startup, "install_sigint_handler", lambda: None)

    with pytest.raises(ModuleNotFoundError):
        startup.run(cli, ["doctor"])
