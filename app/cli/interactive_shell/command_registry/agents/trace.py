"""Live trace rendering and /fleet trace subcommand."""

from __future__ import annotations

import time
from contextlib import nullcontext

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import BOLD_BRAND, DIM, ERROR
from app.fleet_monitoring.registry import AgentRegistry
from app.fleet_monitoring.tail import AttachSession, AttachUnsupported, attach

_TRACE_REFRESH_PER_SECOND = 10
# Match the throttle period to ``Live``'s refresh rate: under a 1k-line/sec
# agent the reader thread can publish chunks faster than Rich actually
# paints, and each ``live.update(Text.from_ansi(...))`` we make in
# excess just creates a Renderable Rich will discard at the next paint.
# Throttling the *call* to ``Live`` to one period bounds CPU under burst
# writers without affecting how fast the screen updates.
_TRACE_RENDER_PERIOD_S = 1.0 / _TRACE_REFRESH_PER_SECOND
# Cap the on-screen render to the most recent slice of the 4 MiB buffer
# so we don't reparse a 4 MiB string through Rich at 10 fps under burst
# writers. A few screens of context is plenty for "what is the agent
# doing right now"; the full tail is still in ``sess.buffer`` for any
# future drill-down view.
_TRACE_RENDER_TAIL_BYTES = 64 * 1024


def _render_trace_snapshot(live: Live, sess: AttachSession) -> None:
    """Decode the bounded snapshot with UTF-8 boundary safety for ``Live``.

    ANSI sequences are interpreted (Rich); treat traced output like unfiltered ``kubectl logs``.
    """
    snapshot = _slice_to_utf8_boundary(sess.buffer.snapshot(), _TRACE_RENDER_TAIL_BYTES)
    live.update(Text.from_ansi(snapshot.decode("utf-8", errors="replace")))


def _slice_to_utf8_boundary(data: bytes, max_bytes: int) -> bytes:
    """Return the suffix of ``data`` that fits in ``max_bytes`` and starts
    on a UTF-8 codepoint boundary.

    A plain ``data[-max_bytes:]`` can land mid-codepoint, which decodes to
    a leading U+FFFD under ``errors="replace"`` — visible as a stray
    replacement character at the top of the live view. ``TailBuffer``
    preserves boundaries by dropping whole chunks; we have to do the same
    once we re-flatten and slice on the render side. UTF-8 continuation
    bytes match ``10xxxxxx`` (``b & 0xC0 == 0x80``); a codepoint is at
    most 4 bytes, so we walk forward at most 3 continuation bytes to
    reach the next start byte.
    """
    if len(data) <= max_bytes:
        return data
    sliced = data[-max_bytes:]
    start = 0
    while start < 4 and start < len(sliced) and (sliced[start] & 0xC0) == 0x80:
        start += 1
    return sliced[start:]


def _render_live_tail(console: Console, label: str, sess: AttachSession) -> None:
    """kubectl-logs-style render: a single Ctrl+C returns to the prompt.

    Catches :class:`KeyboardInterrupt` inside the ``Live`` block and
    swallows it so the REPL doesn't see a traceback. ``stream_to_console``
    in ``streaming.py`` uses a double-press pattern because it's
    rendering an LLM response that the user might *not* want to abort
    on a stray keypress; a logs-style view is the inverse — one press
    is the canonical "stop" signal.
    """
    console.print(f"[{BOLD_BRAND}]trace {escape(label)}[/]  [{DIM}]Ctrl+C to stop[/]")
    isatty = getattr(console.file, "isatty", None)
    stdout_context = patch_stdout(raw=True) if callable(isatty) and isatty() else nullcontext()
    try:
        with (
            stdout_context,
            Live(
                Text(""),
                console=console,
                refresh_per_second=_TRACE_REFRESH_PER_SECOND,
                transient=False,
                vertical_overflow="visible",
            ) as live,
        ):
            # Iterating ``sess`` is what drains the reader queue and
            # appends to ``sess.buffer`` — the loop body only needs the
            # *side effect* of advancing, not the chunk value, so the
            # iteration variable is intentionally discarded.
            # Seeded at 0.0 so the first iteration always renders (any
            # ``time.monotonic() - 0.0`` clears the period); the throttle
            # kicks in from the second iteration onward.
            last_render = 0.0
            pending = False
            for _ in sess:
                now = time.monotonic()
                if now - last_render >= _TRACE_RENDER_PERIOD_S:
                    _render_trace_snapshot(live, sess)
                    last_render = now
                    pending = False
                else:
                    pending = True
            # Final flush: the last chunk(s) may have arrived inside a
            # throttle window; render once after the loop so the user
            # sees the very latest state instead of whatever was on
            # screen at the last gated update.
            if pending:
                _render_trace_snapshot(live, sess)
    except KeyboardInterrupt:
        # kubectl-logs-style: a single Ctrl+C ends the trace and returns
        # to the REPL prompt without propagating a traceback. The
        # ``with sess:`` in the caller still runs and joins the reader
        # thread, so this swallow is safe.
        pass
    if sess.producer_exited:
        # Distinguish "the agent died and we noticed" from "the user
        # asked us to stop" so a long unattended trace doesn't look the
        # same as a Ctrl+C abort.
        console.print(f"[{DIM}]· process exited[/]")
    console.print(f"[{DIM}]· trace ended[/]")


def _cmd_agents_trace(session: ReplSession, console: Console, args: list[str]) -> bool:
    """Live-tail an agent's stdout by pid; see :func:`_render_live_tail`.

    Validates eagerly (``attach()`` raises :class:`AttachUnsupported`
    synchronously on bad pid / unsupported fd type / missing file) so
    we never enter the ``Live`` block on a target we cannot tail.
    """
    if len(args) != 1:
        console.print(f"[{ERROR}]usage:[/] /fleet trace <pid>")
        session.mark_latest(ok=False, kind="slash")
        return True
    try:
        pid = int(args[0])
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(args[0])}")
        session.mark_latest(ok=False, kind="slash")
        return True

    record = AgentRegistry().get(pid)
    label = f"{record.name} (pid {pid})" if record else f"pid {pid}"

    try:
        sess = attach(pid)
    except AttachUnsupported as exc:
        console.print(f"[{ERROR}]cannot trace {escape(label)}:[/] {escape(exc.reason)}")
        session.mark_latest(ok=False, kind="slash")
        return True

    with sess:
        _render_live_tail(console, label, sess)
    return True
