"""Keep the live prompt region compact; reset chrome cleanly on resize.

Root cause
----------
prompt-toolkit sizes a non-fullscreen Screen as::

    height = max(_min_available_height, last_height, preferred_height)

After CPR, ``_min_available_height`` is "rows below the cursor" (the rest of the
terminal under the launch banner). That tall Screen scrolls the banner away,
and ``last_height`` sticks so later paints stay hollow.

Partial ``erase`` on SIGWINCH cannot keep scrollback chrome and the live
region aligned — soft-wrap and reflow leave Auto/composer ghosts. On resize the
host therefore clears the viewport, reprints the static banner, and redraws the
prompt from a clean cursor position.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.output.base import Size

# Soft-wrap headroom above preferred Auto + composer. Keep tiny — blank Screen
# rows below the composer become a hollow band and invite ghost stacking.
_LIVE_REGION_HEIGHT_PAD = 1
# Absolute ceiling; never paint a live Screen taller than this.
_LIVE_REGION_HARD_MAX = 12


def live_region_height_cap(preferred: int) -> int:
    """Return the max Screen height allowed for the live prompt region."""
    return min(max(preferred, 1) + _LIVE_REGION_HEIGHT_PAD, _LIVE_REGION_HARD_MAX)


def prepare_live_region_height(renderer: Any, layout: Layout, *, columns: int, rows: int) -> int:
    """Force CPR / last-screen budgets down so height tracks preferred chrome.

    Returns the live-region cap applied (for tests).
    """
    preferred = layout.container.preferred_height(columns, rows).preferred
    cap = live_region_height_cap(preferred)
    renderer._min_available_height = 0
    last = getattr(renderer, "_last_screen", None)
    if last is not None and int(getattr(last, "height", 0) or 0) > cap:
        renderer._last_screen = None
    return cap


# Back-compat name used by older tests / imports.
clamp_live_region_min_height = prepare_live_region_height


def _size_changed(previous: Size | None, current: Size) -> bool:
    if previous is None:
        return False
    return previous.rows != current.rows or previous.columns != current.columns


def install_shrink_resize_guard(
    app: Application[Any],
    *,
    rerender_banner: Callable[[], None] | None = None,
) -> None:
    """Install height + resize chrome guards for banner-safe layout.

    ``rerender_banner`` clears the viewport and reprints the static launch
    banner at the new size. When provided, resize skips prompt-toolkit's
    partial erase (which stacks Auto/composer ghosts) and redraws the live
    region from the cursor below the fresh banner.
    """
    output = app.output
    renderer = app.renderer
    original_on_resize = app._on_resize
    original_render = renderer.render
    original_report = renderer.report_absolute_cursor_row

    def report_absolute_cursor_row(row: int) -> None:
        original_report(row)
        renderer._min_available_height = 0

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        size = output.get_size()
        if _size_changed(getattr(renderer, "_last_size", None), size):
            renderer._last_screen = None
            renderer._min_available_height = 0
        prepare_live_region_height(
            renderer,
            layout,
            columns=size.columns,
            rows=size.rows,
        )
        original_render(pt_app, layout, is_done)
        output.disable_autowrap()

    def _on_resize() -> None:
        output.disable_autowrap()
        renderer._min_available_height = 0
        renderer._last_screen = None
        if rerender_banner is not None:
            # Full chrome reset: clear + static banner, then redraw the live
            # region only. Do not call original erase — it leaves ghosts.
            rerender_banner()
            renderer.reset(leave_alternate_screen=False)
            app._request_absolute_cursor_position()
            app._redraw()
            output.disable_autowrap()
            return
        original_on_resize()

    renderer.report_absolute_cursor_row = report_absolute_cursor_row  # type: ignore[method-assign]
    renderer.render = _render  # type: ignore[method-assign, assignment]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = [
    "clamp_live_region_min_height",
    "install_shrink_resize_guard",
    "live_region_height_cap",
    "prepare_live_region_height",
]
