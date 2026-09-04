"""Shrink-resize guard for the live prompt region.

Failure modes this module prevents:

1. **Tall live region (banner scrolls away).** After CPR, prompt-toolkit sets
   ``_min_available_height`` to "rows below the cursor". ``Renderer.render``
   then takes ``max(min_available, preferred)``, paints dozens of blank rows,
   and scrolls the launch banner out of the viewport.

2. **Overshot erase (banner wiped).** ``Renderer.erase`` does ``cursor_up(y)``
   then ``erase_down()``. Inflating ``y`` toward the terminal floor starts the
   wipe inside scrollback chrome.

3. **Undershot erase (ghost Auto lines).** When columns shrink, the previous
   paint soft-wraps. Erasing only the logical height leaves wrapped ``Auto``
   rows behind; the next paint stacks another copy.

Clamp the live Screen to preferred chrome size, and on resize inflate erase
``y`` only within that live budget (never into the banner).
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.layout.layout import Layout

# Headroom above preferred Auto + composer height for soft-wrap / completion.
# Keep small: a large budget reintroduces scrolling the banner away.
_LIVE_REGION_HEIGHT_PAD = 4
# Extra erase rows on resize for soft-wrapped ghosts inside the live region.
_RESIZE_ERASE_MAX_EXTRA = 4


def inflate_resize_erase_y(
    logical_y: int,
    *,
    rows: int,
    live_cap: int | None = None,
) -> int:
    """Return erase origin Y: a little above logical height, inside the live cap."""
    if logical_y <= 0:
        return logical_y
    inflated = logical_y + _RESIZE_ERASE_MAX_EXTRA
    if live_cap is not None and live_cap > 0:
        inflated = min(inflated, live_cap)
    ceiling = max(1, rows) - 1
    return min(ceiling, inflated)


def clamp_live_region_min_height(renderer: Any, layout: Layout, *, columns: int, rows: int) -> int:
    """Cap ``_min_available_height`` so the Screen does not fill the terminal.

    Returns the cap applied (preferred + pad), for tests.
    """
    preferred = layout.container.preferred_height(columns, rows).preferred
    cap = max(preferred, 1) + _LIVE_REGION_HEIGHT_PAD
    current = int(getattr(renderer, "_min_available_height", 0) or 0)
    if current > cap:
        renderer._min_available_height = cap
    return cap


def _live_erase_cap(renderer: Any) -> int | None:
    """Max rows erase may climb — last live Screen height plus soft-wrap pad."""
    last = getattr(renderer, "_last_screen", None)
    if last is None:
        return None
    height = int(getattr(last, "height", 0) or 0)
    if height <= 0:
        return None
    return height + _RESIZE_ERASE_MAX_EXTRA


def install_shrink_resize_guard(app: Application[Any]) -> None:
    """Install height + erase + autowrap guards for banner-safe resize."""
    output = app.output
    renderer = app.renderer
    original_on_resize = app._on_resize
    original_render = renderer.render

    def _render(pt_app: Any, layout: Layout, is_done: bool = False) -> None:
        size = output.get_size()
        clamp_live_region_min_height(
            renderer,
            layout,
            columns=size.columns,
            rows=size.rows,
        )
        original_render(pt_app, layout, is_done)
        # prompt_toolkit re-enables autowrap after non-fullscreen paints so
        # background threads can wrap. That leaves the idle composer wrap-on,
        # so a column shrink soft-wraps Ready/Auto into ghost rows. Disable
        # again; ``patch_prompt_stdout`` still calls ``enable_autowrap`` for
        # each background write.
        output.disable_autowrap()

    def _on_resize() -> None:
        output.disable_autowrap()
        pos = renderer._cursor_pos
        if pos is not None and pos.y > 0:
            size = output.get_size()
            inflated_y = inflate_resize_erase_y(
                pos.y,
                rows=size.rows,
                live_cap=_live_erase_cap(renderer),
            )
            renderer._cursor_pos = type(pos)(x=pos.x, y=inflated_y)
        original_on_resize()

    renderer.render = _render  # type: ignore[method-assign, assignment]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = [
    "clamp_live_region_min_height",
    "inflate_resize_erase_y",
    "install_shrink_resize_guard",
]
