"""Live prompt region must stay compact and erase cleanly on resize.

After CPR, prompt-toolkit wants ``_min_available_height`` = rows below the
cursor (often the rest of the screen). Painting that many rows scrolls the
banner away; erasing that many rows on resize wipes it. Undershooting erase
leaves soft-wrapped ``Auto`` ghosts that stack on the next paint.
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
    clamp_live_region_min_height,
    inflate_resize_erase_y,
    install_shrink_resize_guard,
)


@dataclass
class _Cursor:
    x: int
    y: int


@dataclass
class _Screen:
    height: int


def test_inflate_resize_erase_y_pads_inside_live_cap() -> None:
    assert inflate_resize_erase_y(3, rows=30, live_cap=8) == 7
    assert inflate_resize_erase_y(3, rows=30, live_cap=5) == 5
    # Without a live cap, still only add the soft-wrap pad — never *3 toward floor.
    assert inflate_resize_erase_y(4, rows=50) == 8
    assert inflate_resize_erase_y(20, rows=24) == 23


def test_clamp_live_region_min_height_stops_full_terminal_screen() -> None:
    layout = Layout(Window(height=5))
    renderer = MagicMock()
    renderer._min_available_height = 40

    cap = clamp_live_region_min_height(renderer, layout, columns=80, rows=50)

    assert cap == 5 + 4
    assert renderer._min_available_height == 5 + 4


def test_clamp_live_region_min_height_leaves_small_budgets_alone() -> None:
    layout = Layout(Window(height=5))
    renderer = MagicMock()
    renderer._min_available_height = 3

    clamp_live_region_min_height(renderer, layout, columns=80, rows=50)

    assert renderer._min_available_height == 3


def test_shrink_resize_guard_inflates_within_last_screen_height() -> None:
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
    renderer._cursor_pos = _Cursor(x=2, y=3)
    renderer._last_screen = _Screen(height=6)
    renderer._min_available_height = 0
    renderer.render = MagicMock()
    app.renderer = renderer

    seen_y: list[int] = []

    def _original_on_resize() -> None:
        pos = renderer._cursor_pos
        seen_y.append(pos.y if pos is not None else -1)

    app._on_resize = _original_on_resize

    install_shrink_resize_guard(app)
    app._on_resize()

    assert seen_y == [inflate_resize_erase_y(3, rows=30, live_cap=6 + 4)]
    assert seen_y[0] <= 6 + 4


def test_shrink_resize_guard_clamps_min_height_before_render() -> None:
    terminal = io.StringIO()
    output = Vt100_Output(
        terminal,
        get_size=lambda: Size(rows=40, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )
    app: Any = MagicMock()
    app.output = output
    renderer = MagicMock()
    renderer._cursor_pos = _Cursor(x=0, y=1)
    renderer._min_available_height = 35
    renderer._last_screen = None
    calls: list[int] = []

    def _original_render(_app: object, layout: Layout, is_done: bool = False) -> None:
        del is_done
        calls.append(renderer._min_available_height)
        del layout

    renderer.render = _original_render
    app.renderer = renderer
    app._on_resize = MagicMock()

    install_shrink_resize_guard(app)
    app.renderer.render(app, Layout(Window(height=5)))

    assert calls == [5 + 4]


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
    renderer._min_available_height = 0
    renderer._last_screen = None
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
