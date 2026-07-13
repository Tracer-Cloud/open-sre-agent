"""Tests for the lazy semantic color tokens in platform.terminal.theme."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from platform.terminal.theme import (
    BRAND,
    DIM,
    SECONDARY,
    TEXT,
    get_theme,
    list_theme_names,
    set_active_theme,
)


def test_tokens_hash_and_compare_as_resolved_style() -> None:
    """Tokens must never collide on their (empty) underlying str value.

    Rich's ``Style.parse`` is lru_cached on the style string; when every token
    hashed as ``""`` they all shared one cache entry, so ``style=TOKEN`` always
    rendered as whichever token was parsed first in the process.
    """
    set_active_theme("blue")
    theme = get_theme("blue")

    assert SECONDARY == theme.SECONDARY
    assert DIM == theme.DIM
    assert SECONDARY != DIM
    assert hash(SECONDARY) == hash(theme.SECONDARY)
    assert hash(SECONDARY) != hash(DIM)
    assert len({SECONDARY, DIM, BRAND, TEXT}) == 4


def test_rich_renders_each_token_with_its_own_color() -> None:
    """End-to-end: distinct tokens produce distinct truecolor escapes."""
    set_active_theme("blue")
    console = Console(force_terminal=True, color_system="truecolor", legacy_windows=False)

    text = Text()
    text.append("a", style=SECONDARY)
    text.append("b", style=DIM)
    text.append("c", style=BRAND)
    with console.capture() as capture:
        console.print(text)

    output = capture.get()
    for token in (SECONDARY, DIM, BRAND):
        red, green, blue = (int(str(token).lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        assert f"38;2;{red};{green};{blue}m" in output


def test_muted_tokens_are_readable_on_theme_background() -> None:
    """SECONDARY and DIM must keep minimum contrast against the theme BG.

    Regression for the near-invisible onboarding text: DIM #444444 on the
    #0A0A0A background was ~2:1 contrast.
    """

    def _luminance(hex_color: str) -> float:
        def channel(value: int) -> float:
            scaled = value / 255
            return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4

        stripped = hex_color.lstrip("#")
        red, green, blue = (int(stripped[i : i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    def _contrast(foreground: str, background: str) -> float:
        lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)

    for name in list_theme_names():
        theme = get_theme(name)
        assert _contrast(theme.SECONDARY, theme.BG) >= 6.0, name
        assert _contrast(theme.DIM, theme.BG) >= 3.0, name
