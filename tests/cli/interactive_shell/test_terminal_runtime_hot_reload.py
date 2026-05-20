from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.cli.interactive_shell.config import ReplConfig
from app.cli.interactive_shell.runtime.terminal_runtime import dispatch as loop_dispatch
from app.cli.interactive_shell.runtime.terminal_runtime import entrypoint as loop_entrypoint


def _patch_seeded_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop_entrypoint, "render_banner", lambda _console: None)
    monkeypatch.setattr(loop_entrypoint, "run_startup_sweep", lambda: None)
    monkeypatch.setattr(
        loop_entrypoint._prompt_surface,
        "_build_prompt_session",
        lambda: SimpleNamespace(history=object()),
    )
    monkeypatch.setattr(loop_dispatch, "render_submitted_prompt", lambda *_args: None)
    monkeypatch.setattr(
        loop_dispatch._router,
        "route_input",
        lambda *_args: SimpleNamespace(
            route_kind=SimpleNamespace(value="slash"),
            to_event_payload=lambda: {},
        ),
    )
    monkeypatch.setattr(loop_dispatch, "execute_routed_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.cli.interactive_shell.runtime.terminal_runtime.execution.dispatch_slash",
        lambda *_args, **_kwargs: False,
    )


def test_repl_checks_hot_reload_for_seeded_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_seeded_repl(monkeypatch)
    checks: list[int] = []

    class _FakeHotReloadCoordinator:
        def check_and_reload(self, _console: object) -> None:
            checks.append(1)

    monkeypatch.setattr(loop_entrypoint, "HotReloadCoordinator", _FakeHotReloadCoordinator)

    exit_code = asyncio.run(
        loop_entrypoint.repl_main(initial_input="/exit", _config=ReplConfig(reload=True))
    )

    assert exit_code == 0
    assert checks == [1]


def test_repl_skips_hot_reload_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_seeded_repl(monkeypatch)

    class _FailingHotReloadCoordinator:
        def __init__(self) -> None:
            raise AssertionError("hot reload should be disabled")

    monkeypatch.setattr(loop_entrypoint, "HotReloadCoordinator", _FailingHotReloadCoordinator)

    exit_code = asyncio.run(
        loop_entrypoint.repl_main(initial_input="/exit", _config=ReplConfig(reload=False))
    )

    assert exit_code == 0
