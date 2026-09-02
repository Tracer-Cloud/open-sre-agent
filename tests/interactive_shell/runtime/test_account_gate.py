"""First-launch sign-in gate is invoked on production REPL startup paths."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any

from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
import surfaces.interactive_shell.runtime.startup.account_gate as account_gate
from config.repl_config import ReplConfig
from integrations.github.login import GitHubLoginResult
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.sign_in import SignInChoice


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=80)


def test_account_is_signed_in_follows_saved_github_username(monkeypatch: Any) -> None:
    # Patch the package API (what account_gate imports). Patching only
    # ``integrations.github.identity`` misses when another test has bound the
    # name on ``integrations.github`` and shadowed ``__getattr__``.
    monkeypatch.setattr("integrations.github.saved_github_username", lambda: "octocat")
    assert account_gate.account_is_signed_in() is True
    monkeypatch.setattr("integrations.github.saved_github_username", lambda: "")
    assert account_gate.account_is_signed_in() is False


def test_account_login_returns_ok_from_github_device_flow(monkeypatch: Any) -> None:
    shown: list[object] = []

    def _auth(*, on_prompt: Any) -> GitHubLoginResult:
        shown.append(on_prompt)
        return GitHubLoginResult(ok=True, username="octocat")

    monkeypatch.setattr(
        "integrations.github.authenticate_and_configure_github",
        _auth,
    )

    assert account_gate.account_login(console=_console()) is True
    assert shown  # device-code callback was supplied


def test_account_login_returns_false_when_device_flow_fails(monkeypatch: Any) -> None:
    def _auth(*, on_prompt: Any) -> GitHubLoginResult:
        del on_prompt
        raise RuntimeError("denied")

    monkeypatch.setattr(
        "integrations.github.authenticate_and_configure_github",
        _auth,
    )
    console = _console()

    assert account_gate.account_login(console=console) is False
    assert "GitHub sign-in failed" in console.file.getvalue()  # type: ignore[attr-defined]


def test_pass_sign_in_gate_skips_the_prompt_during_tests(monkeypatch: Any) -> None:
    called: list[bool] = []
    monkeypatch.setattr(account_gate, "is_test_run", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.run_sign_in_gate",
        lambda *_a, **_k: called.append(True) or False,
    )

    assert account_gate.pass_sign_in_gate(_console()) is True
    assert called == []


def test_pass_sign_in_gate_runs_the_screen_when_opt_in_is_on(monkeypatch: Any) -> None:
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.setenv("OPENSRE_FORCE_SIGNIN", "1")
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: False)
    monkeypatch.setattr("surfaces.interactive_shell.ui.sign_in.repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.render_sign_in_screen", lambda _c: None
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.prompt_login_or_exit",
        lambda: SignInChoice.EXIT,
    )

    assert account_gate.pass_sign_in_gate(_console()) is False


def test_pass_sign_in_gate_forces_the_screen_even_when_signed_in(monkeypatch: Any) -> None:
    # The opt-in flag is a force: a saved username must not skip the screen, so a
    # signed-in dev can still see it. Exit returns False (the gate did prompt).
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.setenv("OPENSRE_FORCE_SIGNIN", "1")
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: True)
    monkeypatch.setattr("surfaces.interactive_shell.ui.sign_in.repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.render_sign_in_screen", lambda _c: None
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.prompt_login_or_exit",
        lambda: SignInChoice.EXIT,
    )

    assert account_gate.pass_sign_in_gate(_console()) is False


def test_pass_sign_in_gate_is_a_noop_when_opt_in_is_off(monkeypatch: Any) -> None:
    called: list[bool] = []
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.delenv("OPENSRE_FORCE_SIGNIN", raising=False)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.run_sign_in_gate",
        lambda *_a, **_k: called.append(True) or False,
    )

    assert account_gate.pass_sign_in_gate(_console()) is True
    assert called == []


def test_run_repl_invokes_the_sign_in_gate_before_the_controller(monkeypatch: Any) -> None:
    gate_consoles: list[object] = []
    started: list[bool] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "should_paint_launch_banner", lambda: True)
    monkeypatch.setattr(main_entrypoint, "render_terminal_ui", lambda *_a, **_k: None)

    def _gate(console: Console) -> bool:
        gate_consoles.append(console)
        return False

    async def _skip_async(**_kwargs: Any) -> int:
        started.append(True)
        return 0

    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", _gate)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)

    exit_code = main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic"))

    assert exit_code == 0
    assert len(gate_consoles) == 1
    assert started == []


def test_run_repl_clears_screen_before_painting_banner(monkeypatch: Any) -> None:
    """Launch wipes the calling shell so the REPL owns the viewport like Droid."""
    cleared: list[bool] = []
    painted: list[bool] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "should_paint_launch_banner", lambda: True)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda _c: True)
    monkeypatch.setattr(
        main_entrypoint,
        "repl_clear_screen",
        lambda: cleared.append(True),
    )
    monkeypatch.setattr(
        main_entrypoint,
        "render_terminal_ui",
        lambda *_a, **_k: painted.append(True),
    )

    async def _skip_async(**_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)
    main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic"))

    assert cleared == [True]
    assert painted == [True]


def test_run_repl_skips_the_launch_banner_when_the_gate_paints_it(monkeypatch: Any) -> None:
    painted: list[bool] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "should_paint_launch_banner", lambda: False)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda _c: True)
    monkeypatch.setattr(
        main_entrypoint,
        "render_terminal_ui",
        lambda *_a, **_k: painted.append(True),
    )

    async def _skip_async(**_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(main_entrypoint, "run_repl_async", _skip_async)

    main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic"))

    assert painted == []


def test_run_repl_async_does_not_run_the_gate_itself(monkeypatch: Any) -> None:
    # The gate runs once in the synchronous run_repl; the coroutine is the shell
    # body only, so it must not invoke pass_sign_in_gate a second time.
    gated: list[bool] = []
    started: list[bool] = []

    class _Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            del _args, _kwargs

        async def start_interactive_shell(self) -> None:
            started.append(True)

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )
    monkeypatch.setattr(
        main_entrypoint, "pass_sign_in_gate", lambda _c: gated.append(True) or False
    )
    monkeypatch.setattr(main_entrypoint, "offer_loop_suggestions", lambda *_a, **_k: None)
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)

    class _SessionStore:
        def open_store(self, _session: object) -> None:
            return None

        def close(self, _session: object) -> None:
            return None

    monkeypatch.setattr(
        main_entrypoint.SessionManager,
        "for_session",
        lambda _session: _SessionStore(),
    )

    exit_code = asyncio.run(main_entrypoint.run_repl_async())

    assert exit_code == 0
    assert gated == []  # the coroutine never re-runs the gate
    assert started == [True]


def test_should_paint_launch_banner_false_for_unsigned_tty(monkeypatch: Any) -> None:
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.setenv("OPENSRE_FORCE_SIGNIN", "1")
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: False)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.repl_tty_interactive",
        lambda: True,
    )

    assert account_gate.should_paint_launch_banner() is False
