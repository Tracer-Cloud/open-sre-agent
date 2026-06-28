"""PromptSession assembly for the interactive shell."""

from __future__ import annotations

from prompt_toolkit import PromptSession

from interactive_shell.runtime import ReplSession
from interactive_shell.session.prompt_history import load_prompt_history
from interactive_shell.ui.input_prompt.completion import ShellCompleter
from interactive_shell.ui.input_prompt.key_bindings import _build_prompt_key_bindings
from interactive_shell.ui.input_prompt.lexer import ReplInputLexer
from interactive_shell.ui.input_prompt.rendering import (
    _DEFAULT_PLACEHOLDER_ANSI,
    resolve_prompt_placeholder,
)
from interactive_shell.ui.input_prompt.style import _build_prompt_style


def _install_prompt_frame(session: PromptSession[str]) -> PromptSession[str]:
    return session


def _build_prompt_session(session: ReplSession | None = None) -> PromptSession[str]:
    placeholder = (
        (lambda: resolve_prompt_placeholder(session))
        if session is not None
        else _DEFAULT_PLACEHOLDER_ANSI
    )
    return _install_prompt_frame(
        PromptSession(
            completer=ShellCompleter(),
            complete_while_typing=True,
            multiline=True,
            reserve_space_for_menu=8,
            history=load_prompt_history(),
            lexer=ReplInputLexer(),
            key_bindings=_build_prompt_key_bindings(),
            style=_build_prompt_style(),
            erase_when_done=True,
            placeholder=placeholder,
        )
    )


__all__ = [
    "_build_prompt_session",
    "_install_prompt_frame",
]
