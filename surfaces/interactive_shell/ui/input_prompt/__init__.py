"""PromptSession assembly for the interactive shell."""

from __future__ import annotations

from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition, to_filter
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    FloatContainer,
    HSplit,
    VerticalAlign,
    Window,
    to_container,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from surfaces.interactive_shell.prompt_history import load_prompt_history
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.input_prompt.completion import ShellCompleter
from surfaces.interactive_shell.ui.input_prompt.frame import rounded_composer_frame
from surfaces.interactive_shell.ui.input_prompt.key_bindings import _build_prompt_key_bindings
from surfaces.interactive_shell.ui.input_prompt.layout import prompt_line_width
from surfaces.interactive_shell.ui.input_prompt.lexer import ReplInputLexer
from surfaces.interactive_shell.ui.input_prompt.rendering import (
    DEFAULT_PLACEHOLDER_TEXT,
    composer_footer_ansi,
    resolve_prompt_placeholder,
)
from surfaces.interactive_shell.ui.input_prompt.style import _build_prompt_style

_COMPOSER_MAX_EDIT_ROWS = 8
_COMPOSER_MIN_FRAME_ROWS = 3
_COMPOSER_MAX_FRAME_ROWS = _COMPOSER_MAX_EDIT_ROWS + 2


def _limit_editable_height(main_input: HSplit) -> HSplit:
    """Return the editable prompt body sized to its text, capped to a chat viewport."""
    editable_children = main_input.children[1:]
    default_buffer_slot = editable_children[0]
    if not isinstance(default_buffer_slot, ConditionalContainer) or not isinstance(
        default_buffer_slot.content, Window
    ):
        raise RuntimeError("prompt-toolkit input container is missing its editable window")
    # No fixed ``preferred``: it pins the box to one row so it never grows with
    # wrapped/multiline input. The buffer's own content height drives it,
    # clamped to [1, max]; ``dont_extend_height`` keeps it from eating leftover
    # terminal rows (that made the bordered box jump as the status region above
    # it changed). Window stores a Filter, not a raw bool — assign via to_filter.
    default_buffer_slot.content.height = Dimension(min=1, max=_COMPOSER_MAX_EDIT_ROWS)
    default_buffer_slot.content.dont_extend_height = to_filter(True)
    return HSplit(editable_children)


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
    editable_body = _limit_editable_height(main_input.content)
    # Inner surface so the editable rows share INPUT_SURFACE with the border
    # (otherwise the frame looks hollow against the terminal bg).
    surface_body: AnyContainer = HSplit([editable_body], style="class:composer-body")
    composer: AnyContainer = rounded_composer_frame(surface_body)
    footer: AnyContainer = Window(
        FormattedTextControl(lambda: ANSI(composer_footer_ansi())),
        height=1,
        dont_extend_height=True,
        style="class:composer-footer",
    )
    box_rows: list[AnyContainer] = [composer, footer]
    if hide_composer is not None:
        shown = Condition(lambda: not hide_composer())
        composer_container = to_container(composer)

        def _current_composer_rows() -> int:
            preferred = composer_container.preferred_height(
                prompt_line_width(),
                _COMPOSER_MAX_FRAME_ROWS,
            ).preferred
            return max(
                _COMPOSER_MIN_FRAME_ROWS,
                min(preferred, _COMPOSER_MAX_FRAME_ROWS),
            )

        # Swap the box for a blank pad of the SAME height while structured input
        # owns the keyboard. Collapsing to zero height shrinks the region, and a
        # shrinking prompt under patch_stdout leaves stale border fragments — a
        # same-height stand-in overwrites the growing box cleanly instead.
        box_rows = [
            ConditionalContainer(composer, filter=shown),
            ConditionalContainer(footer, filter=shown),
            ConditionalContainer(
                Window(height=lambda: _current_composer_rows() + 1, char=" "),
                filter=~shown,
            ),
        ]
    # Pack status + composer at the top of the live region (no JUSTIFY gap
    # between Auto and the input box). Last column stays empty for wrap safety.
    chrome = HSplit(
        [before_input, *box_rows],
        width=prompt_line_width,
        align=VerticalAlign.TOP,
    )
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
    def _default_placeholder() -> FormattedText:
        from surfaces.interactive_shell.ui.input_prompt.rendering import (
            _placeholder_formatted,
        )

        return _placeholder_formatted(DEFAULT_PLACEHOLDER_TEXT)

    placeholder = (
        (lambda: resolve_prompt_placeholder(session))
        if session is not None
        else _default_placeholder
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
    "rounded_composer_frame",
]
