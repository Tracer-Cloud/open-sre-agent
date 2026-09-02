"""Shrink-resize must erase soft-wrapped ghost rows before redrawing.

When columns shrink, the emulator wraps the previous paint before
``Application._on_resize`` runs. ``Renderer.erase`` only climbs the logical
``_cursor_pos.y``, so wrapped rows stay and the next paint stacks another
Ready line. The guard inflates the erase origin and keeps autowrap off
between paints.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.base import Size
from prompt_toolkit.output.vt100 import Vt100_Output

from surfaces.interactive_shell.ui.input_prompt.resize import (
    inflate_resize_erase_y,
    install_shrink_resize_guard,
)


@dataclass
class _Cursor:
    x: int
    y: int


def test_inflate_resize_erase_y_covers_soft_wrap_multiple() -> None:
    # Under-reported logical y=3 after a wrap that made physical ~9–11 rows.
    assert inflate_resize_erase_y(3, rows=30) >= 9
    assert inflate_resize_erase_y(3, rows=30) >= 3 + 8
    # Never past the terminal floor.
    assert inflate_resize_erase_y(20, rows=24) == 23


def test_shrink_resize_guard_inflates_cursor_before_original_on_resize() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=2, y=3)  # under-reported after soft-wrap
    renderer.render = MagicMock()
    app.renderer = renderer

    seen_y: list[int] = []

    def _original_on_resize() -> None:
        pos = renderer._cursor_pos
        seen_y.append(pos.y if pos is not None else -1)

    app._on_resize = _original_on_resize

    install_shrink_resize_guard(app)
    app._on_resize()

    assert seen_y == [inflate_resize_erase_y(3, rows=30)]


def test_shrink_resize_guard_disables_autowrap_after_render() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=24, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    disabled: list[bool] = []
    real_disable = output.disable_autowrap

    def _spy_disable() -> None:
        disabled.append(True)
        real_disable()

    output.disable_autowrap = _spy_disable  # type: ignore[method-assign]

    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=0, y=1)
    calls: list[str] = []

    def _original_render(*_a: object, **_k: object) -> None:
        calls.append("render")

    renderer.render = _original_render
    app.renderer = renderer
    app._on_resize = MagicMock()

    install_shrink_resize_guard(app)
    disabled.clear()
    app.renderer.render(app, Layout(Window()))
    assert calls == ["render"]
    assert disabled, "render must leave autowrap disabled so shrink cannot soft-wrap the UI"
