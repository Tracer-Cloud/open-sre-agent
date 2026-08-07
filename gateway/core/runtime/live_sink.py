"""Turn-retargetable output sink for session-scoped headless agents.

A gateway session reuses one :class:`HeadlessAgent` across inbound messages.
Transport sinks are per-turn, so this holder stays on the agent and
:meth:`bind` swaps the inner sink before each dispatch.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from typing import Any

from gateway.core.runtime.sink_protocol import GatewaySink


class LiveOutputSink:
    """Delegates :class:`~core.agent_harness.ports.OutputSink` calls to the bound sink.

    Explicit methods (not ``__getattr__``) keep the port structurally typed so
    construction sites do not need ``type: ignore``.
    """

    def __init__(self) -> None:
        self._inner: GatewaySink | None = None

    def bind(self, sink: GatewaySink) -> None:
        """Point subsequent output at ``sink`` for the current turn."""
        self._inner = sink

    @property
    def bound(self) -> GatewaySink | None:
        """Currently bound transport sink, if any."""
        return self._inner

    @property
    def turn_cancel(self) -> threading.Event | None:
        """Same cancel Event as the bound transport sink (one signal, many readers).

        Explicit (not only ``__getattr__``) so
        :func:`~core.agent_harness.turns.host_cancel.host_cancel_requested` and
        the stream guard see cancel without depending on attribute forwarding.
        """
        inner = self._inner
        if inner is None:
            return None
        cancel = getattr(inner, "turn_cancel", None)
        return cancel if isinstance(cancel, threading.Event) else None

    def _require(self) -> GatewaySink:
        inner = self._inner
        if inner is None:
            raise RuntimeError("gateway turn sink not bound")
        return inner

    def print(self, message: str = "") -> None:
        print_fn = getattr(self._require(), "print", None)
        if callable(print_fn):
            print_fn(message)

    def render_response_header(self, label: str) -> None:
        render = getattr(self._require(), "render_response_header", None)
        if callable(render):
            render(label)

    def render_error(self, message: str) -> None:
        self._require().render_error(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        return self._require().stream(
            label=label,
            chunks=self._stop_chunks_on_cancel(chunks),
            suppress_if_starts_with=suppress_if_starts_with,
            defer_want_me_to_closer=defer_want_me_to_closer,
        )

    def _stop_chunks_on_cancel(self, chunks: Iterable[str]) -> Iterator[str]:
        """Stop draining the LLM stream when the host soft-timeout Event fires."""
        for chunk in chunks:
            cancel = self.turn_cancel
            if isinstance(cancel, threading.Event) and cancel.is_set():
                return
            yield chunk

    def finalize(self, text: str) -> None:
        self._require().finalize(text)

    def finish_streamed_response(self, text: str) -> None:
        finish = getattr(self._require(), "finish_streamed_response", None)
        if callable(finish):
            finish(text)
            return
        self.finalize(text)

    def set_tool_status(self, status: str) -> None:
        set_status = getattr(self._require(), "set_tool_status", None)
        if callable(set_status):
            set_status(status)

    def __getattr__(self, name: str) -> Any:
        # Forward optional transport-specific attributes (e.g. tool_hooks readers).
        return getattr(self._require(), name)


__all__ = ["LiveOutputSink"]
