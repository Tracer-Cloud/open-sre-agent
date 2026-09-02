"""Shrink-resize guard for the live prompt region.

When the terminal narrows, the emulator soft-wraps the *previous* paint before
prompt-toolkit's ``_on_resize`` runs. ``Renderer.erase`` only climbs
``_cursor_pos.y`` logical rows, so the extra wrapped rows stay on screen and
the next redraw stacks another Ready / Auto / composer frame (ghost chrome).

Keep autowrap off after each paint (stdout proxy turns it on for prints) and
erase from an inflated origin on resize so soft-wrapped rows are cleared.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.application import Application

# Soft-wrap after a modest shrink often doubles or triples physical rows for
# the same logical UI. Climb at least this many extra rows beyond 3× height.
_RESIZE_ERASE_PAD_ROWS = 8


def inflate_resize_erase_y(logical_y: int, *, rows: int) -> int:
    """Return the erase origin Y that covers soft-wrapped physical rows."""
    if logical_y <= 0:
        return logical_y
    ceiling = max(1, rows) - 1
    return min(ceiling, max(logical_y * 3, logical_y + _RESIZE_ERASE_PAD_ROWS))


def install_shrink_resize_guard(app: Application[Any]) -> None:
    """Install erase + autowrap guards so column shrinks do not ghost the UI."""
    output = app.output
    renderer = app.renderer
    original_on_resize = app._on_resize
    original_render = renderer.render

    def _render(*args: Any, **kwargs: Any) -> None:
        original_render(*args, **kwargs)
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
            inflated_y = inflate_resize_erase_y(pos.y, rows=output.get_size().rows)
            renderer._cursor_pos = type(pos)(x=pos.x, y=inflated_y)
        original_on_resize()

    renderer.render = _render  # type: ignore[method-assign]
    app._on_resize = _on_resize  # type: ignore[method-assign]
    output.disable_autowrap()


__all__ = ["inflate_resize_erase_y", "install_shrink_resize_guard"]
