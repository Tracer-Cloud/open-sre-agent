"""PromptSession assembly for the interactive shell."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

from surfaces.interactive_shell.prompt_history import load_prompt_history
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.input_prompt.completion import ShellCompleter
from surfaces.interactive_shell.ui.input_prompt.key_bindings import _build_prompt_key_bindings
from surfaces.interactive_shell.ui.input_prompt.layout import prompt_line_width
from surfaces.interactive_shell.ui.input_prompt.lexer import ReplInputLexer
from surfaces.interactive_shell.ui.input_prompt.rendering import (
    _DEFAULT_PLACEHOLDER_ANSI,
    composer_footer_ansi,
    resolve_prompt_placeholder,
)
from surfaces.interactive_shell.ui.input_prompt.style import _build_prompt_style


def _install_prompt_frame(
    session: PromptSession[str],
    *,
    hide_composer: Callable[[], bool] | None = None,
) -> PromptSession[str]:
    """Wrap only the editable buffer, leaving live status rows above it.

    ``hide_composer`` (when given) collapses the composer box and its footer
    while structured input owns the keyboard (confirmation choice, option
    menus), so the free-text box does not sit under the pending decision.
    """
    root = session.app.layout.container
    if not isinstance(root, HSplit) or not root.children:
        raise RuntimeError("prompt-toolkit returned an unsupported root layout")
    main_slot = root.children[0]
    if not isinstance(main_slot, ConditionalContainer):
        raise RuntimeError("prompt-toolkit returned an unsupported input layout")
    main_input = main_slot.alternative_content
    if not isinstance(main_input, FloatContainer) or not isinstance(main_input.content, HSplit):
        raise RuntimeError("prompt-toolkit returned an unsupported input container")
    if len(main_input.content.children) < 2:
        raise RuntimeError("prompt-toolkit input container is missing its buffer")

    before_input = main_input.content.children[0]
    editable_body = HSplit(main_input.content.children[1:])
    composer: AnyContainer = Frame(editable_body, style="class:composer", height=3)
    footer: AnyContainer = Window(
        FormattedTextControl(lambda: ANSI(composer_footer_ansi())),
        height=1,
        dont_extend_height=True,
        style="class:composer-footer",
    )
    if hide_composer is not None:
        shown = Condition(lambda: not hide_composer())
        composer = ConditionalContainer(composer, filter=shown)
        footer = ConditionalContainer(footer, filter=shown)
    # Keep the final terminal column empty. Painting a frame border there puts
    # the cursor in pending-wrap, which makes patch_stdout redraws jump and
    # leaves stale composer fragments after output or a terminal resize.
    chrome = HSplit([before_input, composer, footer], width=prompt_line_width)
    framed_input = FloatContainer(
        chrome,
        floats=main_input.floats,
        modal=main_input.modal,
        key_bindings=main_input.key_bindings,
        style=main_input.style,
        z_index=main_input.z_index,
    )
    # Replace the root instead of mutating ``root.children``: HSplit caches its
    # converted child containers, so an in-place list update would leave the
    # original unframed input active even though the object graph looks changed.
    session.layout.container = HSplit([framed_input, *root.children[1:]])
    return session


def build_prompt_session(
    session: Session | None = None,
    *,
    hide_composer: Callable[[], bool] | None = None,
) -> PromptSession[str]:
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
            reserve_space_for_menu=0,
            history=load_prompt_history(),
            lexer=ReplInputLexer(),
            key_bindings=_build_prompt_key_bindings(),
            style=_build_prompt_style(),
            erase_when_done=True,
            placeholder=placeholder,
        ),
        hide_composer=hide_composer,
    )


__all__ = [
    "_install_prompt_frame",
    "build_prompt_session",
]
