"""Rounded prompt-toolkit composer frame."""

from __future__ import annotations

from prompt_toolkit.layout.containers import AnyContainer, HSplit, VSplit, Window


def rounded_composer_frame(body: AnyContainer) -> HSplit:
    """Wrap the composer in the terminal's smallest rounded border."""

    def _border(*, char: str, width: int | None = None) -> Window:
        return Window(
            width=width,
            height=1 if width == 1 else None,
            char=char,
            style="class:frame.border",
        )

    top = VSplit(
        [
            _border(char="╭", width=1),
            _border(char="─"),
            _border(char="╮", width=1),
        ],
        height=1,
    )
    middle = VSplit(
        [
            Window(width=1, char="│", style="class:frame.border"),
            body,
            Window(width=1, char="│", style="class:frame.border"),
        ],
        padding=0,
    )
    bottom = VSplit(
        [
            _border(char="╰", width=1),
            _border(char="─"),
            _border(char="╯", width=1),
        ],
        height=1,
    )
    return HSplit([top, middle, bottom], style="class:frame class:composer")


__all__ = ["rounded_composer_frame"]
