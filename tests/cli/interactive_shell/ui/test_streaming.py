"""Tests for the shared live-streaming renderer used by interactive-shell handlers."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pytest
from rich.console import Console

from app.cli.interactive_shell.ui.streaming import stream_to_console


def _strip_ansi(text: str) -> str:
    """Drop ANSI escapes so assertions check the visible output."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def _tty_console() -> tuple[Console, io.StringIO]:
    """Build a Console that thinks it is a terminal so Rich.Live actually renders."""
    buf = io.StringIO()
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


def _non_tty_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=80), buf


def _yield_chunks(chunks: list[str]) -> Iterator[str]:
    yield from chunks


class TestNonTtyFallback:
    """On a non-terminal console the helper drains, prints, and returns the full text."""

    def test_drains_stream_and_prints_without_live_artifacts(self) -> None:
        console, buf = _non_tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Hel", "lo, ", "world"]),
        )

        output = buf.getvalue()
        assert result == "Hello, world"
        # Header + text reach piped output so captured logs are useful.
        assert "assistant:" in output
        assert "Hello, world" in output
        # No spinner / Live cursor-movement artifacts in non-TTY captures.
        assert "thinking" not in output

    def test_suppression_drains_silently_in_non_tty(self) -> None:
        """Suppressed payloads (JSON action plans) must not appear in piped output."""
        console, buf = _non_tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]}"]),
            suppress_if_starts_with="{",
        )

        assert result == '{"actions":[]}'
        output = buf.getvalue()
        assert "assistant:" not in output
        assert '{"actions"' not in output


class TestTtyLiveRender:
    """On a terminal console the response renders live and the final text stays visible."""

    def test_renders_label_and_streamed_content_as_markdown(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Run **opensre", " investigate** to start."]),
        )

        output = _strip_ansi(buf.getvalue())
        assert result == "Run **opensre investigate** to start."
        # Header is pinned above the live region.
        assert "assistant:" in output
        # Markdown is rendered live; the literal ** delimiters must not survive.
        assert "**opensre" not in output
        assert "opensre investigate" in output

    def test_returns_empty_string_when_stream_is_empty(self) -> None:
        """An empty stream must not leave a frozen spinner on screen."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks([]),
        )

        assert result == ""
        # Header still printed, but no thinking-spinner residue at finalize.
        assert "assistant:" in _strip_ansi(buf.getvalue())


class TestMidStreamError:
    """Errors inside the stream propagate while the partial buffer stays on screen."""

    def test_exception_propagates_with_partial_visible(self) -> None:
        def _broken_stream() -> Iterator[str]:
            yield "partial "
            yield "answer"
            raise RuntimeError("upstream 503")

        console, buf = _tty_console()

        with pytest.raises(RuntimeError, match="upstream 503"):
            stream_to_console(
                console,
                label="assistant",
                chunks=_broken_stream(),
            )

        # The partial response was rendered before the exception propagated,
        # so the caller can surface an error label below it.
        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output

    def test_single_keyboard_interrupt_is_noted_and_stream_completes(self) -> None:
        """A single Ctrl+C mid-stream is absorbed (footer hint pinned) and the
        stream finishes naturally; the partial buffer is returned.

        This reflects the double-press cancellation contract introduced for
        the terminal CLI: one press warns, a second within the window aborts.
        """

        class _ChunksThenSingleKbd:
            __slots__ = ("_i", "_raised")

            def __init__(self) -> None:
                self._i = 0
                self._raised = False

            def __iter__(self) -> Iterator[str]:
                return self

            def __next__(self) -> str:
                parts = ("partial ", "answer")
                if self._i < len(parts):
                    c = parts[self._i]
                    self._i += 1
                    return c
                if not self._raised:
                    self._raised = True
                    raise KeyboardInterrupt
                raise StopIteration

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=iter(_ChunksThenSingleKbd()),
        )

        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output
        assert "Press Ctrl+C again to stop" in output
        assert result == "partial answer"

    def test_double_keyboard_interrupt_propagates(self) -> None:
        """Two Ctrl+C presses within the window cancel the stream and re-raise.

        The partial buffer rendered before the cancellation must remain on
        screen so the caller can label it as cancelled.
        """

        class _ChunksThenDoubleKbd:
            __slots__ = ("_i",)

            def __init__(self) -> None:
                self._i = 0

            def __iter__(self) -> Iterator[str]:
                return self

            def __next__(self) -> str:
                parts = ("partial ", "answer")
                if self._i < len(parts):
                    c = parts[self._i]
                    self._i += 1
                    return c
                # Every subsequent call raises — emulates two Ctrl+C presses
                # firing back-to-back within the double-press window.
                raise KeyboardInterrupt

        console, buf = _tty_console()
        with pytest.raises(KeyboardInterrupt):
            stream_to_console(
                console,
                label="assistant",
                chunks=iter(_ChunksThenDoubleKbd()),
            )

        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output
        assert "Press Ctrl+C again to stop" in output


class TestTimingFooter:
    """A small dim ``· Ns`` footer appears after a rendered live response."""

    def test_footer_printed_after_streamed_response(self) -> None:
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["hello"]),
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is not None

    def test_footer_skipped_when_stream_is_empty(self) -> None:
        """Empty stream must not print a timing footer under nothing."""
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks([]),
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is None

    def test_footer_skipped_when_response_is_suppressed(self) -> None:
        """Suppressed JSON action plans should not get a timing footer either."""
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]}"]),
            suppress_if_starts_with="{",
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is None


class TestSuppressionPeek:
    """``suppress_if_starts_with`` skips live rendering for content the caller will handle."""

    def test_suppresses_and_drains_when_first_char_matches(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]", "}"]),
            suppress_if_starts_with="{",
        )

        assert result == '{"actions":[]}'
        # No header, no markdown, no live-region artifacts in captured output.
        output = _strip_ansi(buf.getvalue())
        assert "assistant:" not in output
        assert '{"actions"' not in output

    def test_renders_normally_when_first_char_does_not_match(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Hello, ", "world"]),
            suppress_if_starts_with="{",
        )

        assert result == "Hello, world"
        output = _strip_ansi(buf.getvalue())
        assert "assistant:" in output
        assert "Hello, world" in output

    def test_skips_leading_whitespace_before_deciding(self) -> None:
        """Leading whitespace must not block the suppression peek."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["  \n", '{"action"', ':"slash"}']),
            suppress_if_starts_with="{",
        )

        assert result == '  \n{"action":"slash"}'
        output = _strip_ansi(buf.getvalue())
        assert "assistant:" not in output


class TestMarkdownReparseThrottle:
    """The Markdown re-parse on every chunk is O(n²) total — long streams stalled.

    These tests pin the throttle behavior: ``Markdown(buffer)`` is constructed
    at most once per refresh window plus a final flush, regardless of how
    many chunks arrive. They use a fake clock + spy on ``Markdown`` so the
    parse count is deterministic and the test runs in microseconds.
    """

    def _install_clock_and_spy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[list[float], list[int]]:
        """Patch ``time.monotonic`` and ``Markdown`` in the streaming module.

        Returns ``(fake_time, parse_count)`` lists — single-element lists used
        as mutable cells. Tests set ``fake_time[0]`` to advance the clock and
        read ``parse_count[0]`` to assert how often the buffer was re-parsed.
        """
        from app.cli.interactive_shell.ui import streaming as streaming_module

        fake_time = [0.0]
        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        monkeypatch.setattr(streaming_module.time, "monotonic", lambda: fake_time[0])
        monkeypatch.setattr(streaming_module, "Markdown", _SpyMarkdown)
        return fake_time, parse_count

    def test_chunks_in_one_throttle_window_collapse_to_one_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """100 chunks within the same refresh window → exactly one final flush."""
        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        console, _ = _tty_console()

        # Clock never advances → throttle blocks every intra-loop render.
        chunks = (f"chunk{i} " for i in range(100))
        result = stream_to_console(console, label="assistant", chunks=chunks)

        assert "chunk0" in result
        assert "chunk99" in result
        # Only the final flush triggers a Markdown parse.
        assert parse_count[0] == 1, (
            f"expected 1 parse (final flush), got {parse_count[0]}; "
            "throttle is letting intra-window updates through"
        )
        # silence unused-var warning while keeping the fixture wired.
        assert fake_time[0] == 0.0

    def test_chunks_across_many_windows_render_periodically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chunks spaced past the throttle interval render multiple times."""
        from app.cli.interactive_shell.ui import streaming as streaming_module

        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        console, _ = _tty_console()
        interval = streaming_module._LIVE_RENDER_INTERVAL_S

        def chunks() -> Iterator[str]:
            # Each chunk advances the clock 2× the throttle interval, so
            # every chunk crosses a new render window.
            for i in range(10):
                fake_time[0] = i * (interval * 2)
                yield f"chunk{i} "

        stream_to_console(console, label="assistant", chunks=chunks())

        # The first chunk lands at fake_time=0 with last_render=0, so it
        # fails the gate (0 - 0 not >= interval) and skips its render.
        # The remaining 9 chunks each cross a fresh render window, then
        # the final flush in the inner ``finally`` adds one more parse.
        # Net: 9 in-loop renders + 1 final flush = 10 total parses.
        # Range allows ±2 for any clock-edge ambiguity if interval drifts.
        assert 8 <= parse_count[0] <= 12, (
            f"expected ~10 parses (9 in-loop + 1 final flush), got {parse_count[0]}"
        )
        # Render count must stay << total chunks; the throttle is what
        # this test exists to prove.
        assert parse_count[0] < 50

    def test_final_flush_renders_chunks_pending_in_last_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Last chunks within the trailing throttle window must still appear."""
        fake_time, parse_count = self._install_clock_and_spy(monkeypatch)
        console, buf = _tty_console()

        # Two batches: render allowed at chunk 1 (clock at 1.0s), then
        # remaining chunks fall within the same 1.0s window so the
        # throttle blocks them mid-loop.
        def chunks() -> Iterator[str]:
            fake_time[0] = 1.0
            yield "early "
            # Clock stays at 1.0 — every following chunk is intra-window.
            yield "tail-1 "
            yield "tail-2 "
            yield "tail-3"

        stream_to_console(console, label="assistant", chunks=chunks())

        output = _strip_ansi(buf.getvalue())
        # All four chunks must appear; the final flush is what guarantees
        # the trailing intra-window content.
        assert "early tail-1 tail-2 tail-3" in output
        # Two parses total: one mid-loop render at the first chunk + one
        # final flush.
        assert parse_count[0] == 2

    def test_partial_buffer_visible_when_stream_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mid-stream exception must still flush the buffered partial response.

        Regression guard for the throttle path: chunks waiting in the
        last window were previously rendered on every chunk; with the
        throttle, an exception could land before the next render and
        drop visible content. The inner ``finally`` flushes before Live
        closes.
        """
        fake_time, _ = self._install_clock_and_spy(monkeypatch)
        console, buf = _tty_console()

        def broken_stream() -> Iterator[str]:
            # Clock never advances → throttle blocks the per-chunk render
            # for both yields. The final flush in the inner finally is
            # what saves us.
            yield "partial "
            yield "answer"
            raise RuntimeError("upstream 503")

        with pytest.raises(RuntimeError, match="upstream 503"):
            stream_to_console(console, label="assistant", chunks=broken_stream())

        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output
        # silence unused-var warning while keeping the fixture wired.
        assert fake_time[0] == 0.0
