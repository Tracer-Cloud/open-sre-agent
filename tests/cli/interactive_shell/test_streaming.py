"""Tests for the shared live-streaming renderer used by interactive-shell handlers."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pytest
from rich.console import Console

from app.cli.interactive_shell.streaming import stream_to_console


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

    def test_keyboard_interrupt_propagates_with_partial_visible(self) -> None:
        """Ctrl+C mid-stream surfaces partial text so the caller can report cancellation."""

        def _interrupted_stream() -> Iterator[str]:
            yield "partial "
            yield "answer"
            raise KeyboardInterrupt

        console, buf = _tty_console()

        with pytest.raises(KeyboardInterrupt):
            stream_to_console(
                console,
                label="assistant",
                chunks=_interrupted_stream(),
            )

        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output


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
