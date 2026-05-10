"""Tests for the shared live-streaming renderer used by interactive-shell handlers."""

from __future__ import annotations

import io
import re
import threading
from collections.abc import Iterator

import pytest
from rich.console import Console

from app.cli.interactive_shell.streaming import (
    format_token_count_short,
    stream_to_console,
)


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
        # Bullet header + label + text reach piped output so captured
        # logs are useful. ``●`` is the row marker; ``assistant`` is the
        # dim label alongside it.
        assert "●" in output
        assert "assistant" in output
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
        # No bullet header for suppressed responses.
        assert "●" not in output
        assert '{"actions"' not in output


class TestTtyParagraphRender:
    """On a terminal console paragraphs render as Markdown the moment
    each ``\\n\\n`` boundary closes them; the final paragraph is
    force-flushed at end-of-stream. Code blocks are kept whole (we
    don't split mid-fence). The spinner indicator drives the live
    streaming feedback within a paragraph.
    """

    def test_renders_label_and_streamed_content_as_markdown(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Run **opensre", " investigate** to start."]),
        )

        output = _strip_ansi(buf.getvalue())
        assert result == "Run **opensre investigate** to start."
        # Bullet row marker pinned above the rendered paragraph.
        assert "●" in output
        # End-of-stream force-flush rendered Markdown — ``**`` stripped.
        assert "**opensre" not in output
        assert "opensre investigate" in output

    def test_renders_first_paragraph_before_second_completes(self) -> None:
        """A complete paragraph (``\\n\\n``) flushes immediately, even
        when more chunks would still arrive after it. The second
        paragraph stays buffered until its own boundary or EOS."""
        chunks: list[str] = []

        def _capture_chunks() -> Iterator[str]:
            for c in ["First **para**.\n\n", "Second **para**."]:
                chunks.append(c)
                yield c

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_capture_chunks(),
        )

        output = _strip_ansi(buf.getvalue())
        assert result == "First **para**.\n\nSecond **para**."
        # Both paragraphs are rendered (``**`` stripped).
        assert "First para." in output
        assert "Second para." in output
        assert "**para**" not in output

    def test_open_code_block_is_not_split_mid_fence(self) -> None:
        """``\\n\\n`` inside an open code block must NOT trigger a
        flush — splitting would render a partial fenced block whose
        formatting breaks. The fence stays whole until it closes."""
        chunks_with_open_fence = [
            "Header\n\n",
            "```python\n",
            "x = 1\n\n",  # blank line inside code block — must not flush
            "y = 2\n",
            "```\n\n",
            "Trailing.",
        ]

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(chunks_with_open_fence),
        )

        # Full text returned unchanged.
        assert "x = 1" in result
        assert "y = 2" in result
        # Both code lines must appear in the rendered output (i.e. the
        # fence wasn't split before its closing ``` was seen).
        output = _strip_ansi(buf.getvalue())
        assert "x = 1" in output
        assert "y = 2" in output
        assert "Trailing" in output

    def test_returns_empty_string_when_stream_is_empty(self) -> None:
        """An empty stream must not leave a frozen spinner on screen."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks([]),
        )

        assert result == ""
        # Bullet still printed (header fires before chunk processing),
        # but no spinner residue at finalize.
        assert "●" in _strip_ansi(buf.getvalue())


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
        """KeyboardInterrupt mid-stream propagates after the partial renders.

        The double-press absorption logic that used to live here was moved
        to the prompt_toolkit cancel key bindings (see
        :func:`app.cli.interactive_shell.loop._build_cancel_key_bindings`)
        — the streaming code just lets ``KeyboardInterrupt`` propagate,
        and the ``finally`` block in :func:`stream_to_console` ensures
        the partial buffer is rendered.
        """

        class _ChunksThenKbd:
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
                raise KeyboardInterrupt

        console, buf = _tty_console()
        with pytest.raises(KeyboardInterrupt):
            stream_to_console(
                console,
                label="assistant",
                chunks=iter(_ChunksThenKbd()),
            )

        output = _strip_ansi(buf.getvalue())
        # Partial is rendered before the KI propagates — the ``finally``
        # in stream_to_console fires the Markdown render of the buffer.
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


class TestFormatTokenCountShort:
    """Shared helper used by both the streaming footer and the live spinner."""

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, "0"),
            (1, "1"),
            (999, "999"),
            (1000, "1.0k"),
            (1234, "1.2k"),
            (10000, "10.0k"),
            (123456, "123.5k"),
        ],
    )
    def test_formats_at_boundaries(self, count: int, expected: str) -> None:
        assert format_token_count_short(count) == expected


class _ProgressConsole(Console):
    """Console with the loop's :class:`_StreamingConsole` shape — exposes
    ``update_streaming_progress`` and ``cancel_requested`` for the
    streaming layer's ``getattr`` dispatch.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        cancel_after_n_progress_calls: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.progress_calls: list[int] = []
        self._cancel_event = cancel_event or threading.Event()
        self._cancel_after = cancel_after_n_progress_calls

    def update_streaming_progress(self, bytes_received: int) -> None:
        self.progress_calls.append(bytes_received)
        if self._cancel_after is not None and len(self.progress_calls) >= self._cancel_after:
            self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()


class TestProgressHook:
    """``stream_to_console`` invokes the optional ``update_streaming_progress``
    hook on the console and throttles the call rate so worker-thread → UI
    cross-thread queueing isn't flooded on long streams.
    """

    def test_progress_hook_called_with_running_byte_count(self) -> None:
        buf = io.StringIO()
        console = _ProgressConsole(file=buf, force_terminal=True, color_system=None, width=80)
        chunks = ["Hello, ", "world", "!"]
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(chunks),
        )

        assert result == "Hello, world!"
        assert console.progress_calls, "progress hook never fired"
        # Counts must be monotonically non-decreasing — the streaming
        # layer pushes a *running* byte total, never a per-chunk delta.
        assert console.progress_calls == sorted(console.progress_calls)
        # Each reported count must reflect bytes that *had* arrived by
        # that point in the stream — never exceed the final total.
        assert console.progress_calls[-1] <= len(result)

    def test_progress_hook_throttled_on_burst_streams(self) -> None:
        """A burst of 200 small chunks must not produce 200 hook calls.

        Throttling target is ~10/s; the test stream finishes well under
        a second so we expect a small handful of calls (not one per
        chunk). The exact count is timing-dependent — assert ``<= 50``
        as a generous upper bound that still proves throttling fires.
        """
        buf = io.StringIO()
        console = _ProgressConsole(file=buf, force_terminal=True, color_system=None, width=80)
        burst = ["x"] * 200
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(burst),
        )

        assert len(console.progress_calls) <= 50, (
            f"throttle did not fire — got {len(console.progress_calls)} calls"
        )

    def test_no_hook_when_console_lacks_method(self) -> None:
        """Plain ``Console`` (no progress method) must stream cleanly."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["alpha", "beta"]),
        )
        assert result == "alphabeta"

    def test_progress_hook_failure_does_not_truncate_response(self) -> None:
        """A flaky status widget must never lose response content."""

        class _BrokenConsole(Console):
            def __init__(self) -> None:
                super().__init__(
                    file=io.StringIO(),
                    force_terminal=True,
                    color_system=None,
                    width=80,
                )

            def update_streaming_progress(self, bytes_received: int) -> None:  # noqa: ARG002
                raise RuntimeError("widget gone")

        console = _BrokenConsole()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["full ", "answer"]),
        )
        assert result == "full answer"


class TestCancelPolling:
    """``stream_to_console`` polls ``console.cancel_requested`` between
    chunks so an Esc-driven cancel signal stops the worker-thread stream
    before it drains the iterator.
    """

    def test_cancel_set_before_stream_returns_empty_partial(self) -> None:
        buf = io.StringIO()
        cancel_event = threading.Event()
        cancel_event.set()  # cancel before any chunk is pulled
        console = _ProgressConsole(
            cancel_event=cancel_event,
            file=buf,
            force_terminal=True,
            color_system=None,
            width=80,
        )

        # If the cancel poll didn't work, the iterator below would
        # raise (it's a single-use generator).
        chunks_iter = _yield_chunks(["a", "b", "c"])
        result = stream_to_console(console, label="assistant", chunks=chunks_iter)
        assert result == ""

    def test_cancel_mid_stream_truncates_buffer(self) -> None:
        """Cancel signalled mid-stream stops further chunk reads.

        Uses a generator that flips the cancel flag from inside its own
        yield loop — that's deterministic regardless of throttling, since
        the next iteration of ``stream_to_console``'s loop checks the
        cancel flag *before* pulling the next chunk.
        """
        buf = io.StringIO()
        cancel_event = threading.Event()
        console = _ProgressConsole(
            cancel_event=cancel_event,
            file=buf,
            force_terminal=True,
            color_system=None,
            width=80,
        )

        chunks_yielded: list[int] = []

        def _chunks_with_cancel() -> Iterator[str]:
            for i in range(20):
                chunks_yielded.append(i)
                if i == 3:
                    cancel_event.set()
                yield f"chunk{i} "

        result = stream_to_console(console, label="assistant", chunks=_chunks_with_cancel())

        # The generator should not have been pumped through to chunk 19 —
        # ``stream_to_console`` should have broken out of its loop once
        # the cancel event was visible.
        assert max(chunks_yielded) < 19, (
            f"generator yielded too many chunks — got up to {max(chunks_yielded)}"
        )
        # The result must include chunks read before the cancel was
        # observed and must not include the trailing chunks.
        assert result.startswith("chunk0 ")
        assert "chunk19" not in result

    def test_no_cancel_attr_means_stream_runs_to_completion(self) -> None:
        """A console without ``cancel_requested`` must drain normally."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["one ", "two ", "three"]),
        )
        assert result == "one two three"


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
        # No bullet header, no markdown, no live-region artifacts.
        output = _strip_ansi(buf.getvalue())
        assert "●" not in output
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
        assert "●" in output
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
        assert "●" not in output
