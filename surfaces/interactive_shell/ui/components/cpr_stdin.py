"""CPR (cursor position report) stdin hygiene for the interactive REPL loop."""

from __future__ import annotations

import os
import re
import select
import sys

# A leaked cursor-position reply is ``ESC[row;colR`` (8-bit CSI ``\x9b`` too); when it
# leaks into the input stream the ESC and/or ``[`` introducer can be lost. The
# introducer-less branches below are constrained so they only fire on genuine CPR
# context. Without that constraint they can silently strip legitimate input such as
# ``5R3``, ``12;34R okay`` or ``12;34R5 nodes``.
_CPR_SEQUENCE_RE = re.compile(
    r"(?:\x1b\[|\x9b)\d{1,4};\d{1,4}R"  # ESC [ row ; col R (introducer present)
    r"|\[\d{1,4};\d{1,4}R"  # [row;colR without ESC (leaked into input)
    r"|\d{1,4};\d{1,4}R(?=[\[\x1b\x9b]|\d{1,4};\d{1,4}R)"  # bare row;colR before another fragment
    r"|\d{1,4}R(?=\[|\x1b|\x9b|\d{1,4};\d{1,4}R)"  # bare rowR before another fragment
    r"|\d{1,4};\d{1,4}R$"  # bare row;colR alone at end of the line
)
_CPR_ESCAPED_SEQUENCE_RE = re.compile(r"(?:\x1b\[|\x9b)\d{1,4};\d{1,4}R")


def drain_stale_cpr_bytes(*, settle_seconds: float = 0.0) -> None:
    """Discard CPR escape-sequence bytes left in stdin after prompt teardown.

    When ``prompt_async`` returns, prompt_toolkit tears down its input-reader
    thread. CPR responses (``ESC[row;colR``) that the bottom-toolbar refresh
    sent but that arrived just after the reader stopped sit in the OS stdin
    buffer and appear as literal keystrokes in the next prompt. This function
    non-blockingly drains stdin between ``prompt_async`` calls on POSIX TTYs.

    ``settle_seconds`` blocks up to that long waiting for a first byte before
    draining, so a CPR reply still in flight across a prompt handoff (e.g. the
    raw investigation-feedback menu returning to the REPL prompt) is caught
    rather than leaking into the next prompt. Once any byte arrives the rest is
    drained non-blockingly. Only used at handoffs where the user has just made a
    selection and is not mid-keystroke.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        return
    try:
        fd = sys.stdin.fileno()
        wait = settle_seconds
        while select.select([fd], [], [], wait)[0]:
            chunk = os.read(fd, 256)
            if not chunk:
                break
            wait = 0.0  # first reply seen; drain any remainder without blocking
    except OSError:
        # Draining stdin is best-effort; ignore when the fd is not readable.
        pass


def strip_cpr_sequences(text: str | None) -> str:
    """Remove terminal cursor-position replies that leaked into submitted text."""
    if not text:
        return ""
    return _CPR_SEQUENCE_RE.sub("", text)


def strip_cpr_escape_sequences(text: str | None) -> str:
    """Remove only canonical escaped CPR sequences from text."""
    if not text:
        return ""
    return _CPR_ESCAPED_SEQUENCE_RE.sub("", text)


def contains_cpr_sequence(text: str | None) -> bool:
    return bool(text and _CPR_SEQUENCE_RE.search(text))


__all__ = [
    "contains_cpr_sequence",
    "drain_stale_cpr_bytes",
    "strip_cpr_escape_sequences",
    "strip_cpr_sequences",
]
