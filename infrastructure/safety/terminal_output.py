"""Strip terminal control characters from model-supplied text.

Plan steps, menu titles, and option labels come from the model and are later
written into raw ANSI output. An embedded escape (ESC, OSC, CR) could spoof the
terminal or corrupt menu-row accounting, so this removes control characters at
the parse boundary, before the text is stored or rendered.
"""

from __future__ import annotations


def strip_terminal_controls(text: str) -> str:
    """Return ``text`` without C0/C1 control characters or DEL.

    Removes ``0x00``–``0x1F`` (including ESC, CR, LF, and Tab), ``0x7F``, and
    ``0x80``–``0x9F``. All printable content, Unicode included, is preserved.
    """
    return "".join(ch for ch in text if not (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F))


__all__ = ["strip_terminal_controls"]
