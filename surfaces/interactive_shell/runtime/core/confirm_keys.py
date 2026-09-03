"""Single-keypress confirmation reader for turns where the arrow-nav prompt app
is unavailable (a subprocess owns the TTY).

Reading in cbreak mode disables terminal echo, so arrow keys and other escape
sequences no longer leak as raw ``^[[A`` garbage — they are swallowed and the
loop waits for a real choice (a row tag, a 1-based digit, the row's answer key,
or Enter for the default). Falls back to a cooked ``input()`` line where no
interactive TTY is available (non-TTY, or a platform without ``termios``).
"""

from __future__ import annotations

import sys

#: Row shape shared with the confirmation gate: ``(answer_key, label)`` pairs.
ConfirmRows = tuple[tuple[str, str], ...]


def resolve_confirm_answer(key: str, rows: ConfirmRows) -> str | None:
    """Map one confirmation keypress to a row answer, or ``None`` to keep waiting.

    Accepts a row tag (``a``/``b``/…), a 1-based digit, or the row's own answer
    key; an empty key (Enter) takes the arrow-nav default — the last row, which
    is always cancel. Any other key returns ``None`` so the caller ignores it.
    """
    normalized = key.strip().lower()
    if not normalized:
        return rows[-1][0]
    for index, (answer, _label) in enumerate(rows):
        if normalized in {chr(ord("a") + index), str(index + 1), answer}:
            return answer
    return None


def read_confirm_answer(prompt: str, rows: ConfirmRows) -> str:
    """Print the rows, read one confirmation choice, and return its answer key."""
    for index, (_answer, label) in enumerate(rows):
        print(f"  [{chr(ord('a') + index)}] {label}")
    tags = "/".join(chr(ord("a") + index) for index in range(len(rows)))
    cancel = rows[-1][0]
    if not _stdin_is_interactive_tty():
        return _read_line_answer(prompt, rows, tags, cancel)
    try:
        return _read_key_answer(prompt, rows, tags, cancel)
    except (ImportError, OSError):
        # No termios (e.g. Windows) or the TTY rejected raw mode.
        return _read_line_answer(prompt, rows, tags, cancel)


def _stdin_is_interactive_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def _read_key_answer(prompt: str, rows: ConfirmRows, tags: str, cancel: str) -> str:
    """Read one keypress in cbreak mode (echo off); swallow arrow/escape keys."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    sys.stdout.write(f"{prompt} [{tags}] ")
    sys.stdout.flush()
    answer = cancel
    try:
        tty.setcbreak(fd)
        while True:
            try:
                char = sys.stdin.read(1)
            except KeyboardInterrupt:
                break
            if not char:
                break
            if char == "\x1b":
                # Arrow/nav escape (ESC [ A/B/C/D): drain the CSI tail, ignore.
                if sys.stdin.read(1) == "[":
                    sys.stdin.read(1)
                continue
            if char in ("\r", "\n"):
                break
            resolved = resolve_confirm_answer(char, rows)
            if resolved is not None:
                answer = resolved
                break
            # Unknown printable key: ignore and keep waiting.
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    sys.stdout.write(f"{answer}\n")
    sys.stdout.flush()
    return answer


def _read_line_answer(prompt: str, rows: ConfirmRows, tags: str, cancel: str) -> str:
    """Cooked one-line read for non-TTY / no-termios environments."""
    try:
        raw = input(f"{prompt} [{tags}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return cancel
    resolved = resolve_confirm_answer(raw, rows)
    return resolved if resolved is not None else raw


__all__ = ["ConfirmRows", "read_confirm_answer", "resolve_confirm_answer"]
