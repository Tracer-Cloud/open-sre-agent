from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.cli.interactive_shell import loop
from app.cli.interactive_shell.config import ReplConfig


def _patch_seeded_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "render_banner", lambda _console: None)
    monkeypatch.setattr(loop, "run_startup_sweep", lambda: None)
    monkeypatch.setattr(
        loop._prompt_surface,
        "_build_prompt_session",
        lambda: SimpleNamespace(history=object()),
    )
    monkeypatch.setattr(loop._prompt_surface, "render_submitted_prompt", lambda *_args: None)
    monkeypatch.setattr(
        loop._router,
        "route_input",
        lambda *_args: SimpleNamespace(
            route_kind=SimpleNamespace(value="slash"),
            to_event_payload=lambda: {},
        ),
    )
    monkeypatch.setattr(loop._commands, "dispatch_slash", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop, "get_analytics", lambda: SimpleNamespace(capture=lambda *_args: None))


def test_repl_checks_hot_reload_for_seeded_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_seeded_repl(monkeypatch)
    checks: list[int] = []

    class _FakeHotReloadCoordinator:
        def check_and_reload(self, _console: object) -> None:
            checks.append(1)

    monkeypatch.setattr(loop, "HotReloadCoordinator", _FakeHotReloadCoordinator)

    exit_code = asyncio.run(loop._repl_main(initial_input="/exit", _config=ReplConfig(reload=True)))

    assert exit_code == 0
    assert checks == [1]


def test_repl_skips_hot_reload_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_seeded_repl(monkeypatch)

    class _FailingHotReloadCoordinator:
        def __init__(self) -> None:
            raise AssertionError("hot reload should be disabled")

    monkeypatch.setattr(loop, "HotReloadCoordinator", _FailingHotReloadCoordinator)

    exit_code = asyncio.run(
        loop._repl_main(initial_input="/exit", _config=ReplConfig(reload=False))
    )

    assert exit_code == 0
