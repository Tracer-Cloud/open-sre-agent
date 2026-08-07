"""Shared body-size limit constant and ASGI streaming enforcement middleware.

Why middleware instead of per-route guards
------------------------------------------
* ``/investigate`` and ``POST /api/investigations`` use FastAPI's Pydantic model
  binding, which reads the request body before any route code runs.  A per-route
  guard added *after* binding comes too late — the bytes are already buffered.
* ASGI middleware wraps the ``receive`` callable that FastAPI/Starlette calls
  internally, so every chunk passes through our counter first.  Once the
  cumulative size exceeds ``MAX_BODY_BYTES`` the middleware short-circuits: it
  immediately sends a ``413`` response and stops reading from the socket — the
  payload is **never fully buffered**, bounding peak memory regardless of what
  ``Content-Length`` says (or doesn't say).

How the single-pass interception works
---------------------------------------
We simultaneously wrap both ``receive`` and ``send``:

* The wrapped ``receive`` counts bytes in each ``http.request`` chunk.  As soon
  as the running total exceeds ``MAX_BODY_BYTES`` it sets a flag, drains the
  receive side by returning ``more_body=False``, and suppresses further calls.
* The wrapped ``send`` buffers any ``http.response.start`` message (headers)
  until the body has been fully received and the oversize flag is checked.  If
  the flag is set, we discard the inner app's response and write our own ``413``
  to the real transport; otherwise we flush the buffered headers and forward all
  subsequent ``send`` messages normally.

This single-pass design means we never call the inner app twice and we never
write real headers to the transport before we decide which response to send.

Usage
-----
::

    from gateway.http.limits import BodySizeLimitMiddleware, MAX_BODY_BYTES

    app.add_middleware(BodySizeLimitMiddleware)
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Cap on POST body size accepted from any caller (authed or not).  Realistic
# alert payloads top out around 50 KB, so 1 MiB is ~20× headroom.
MAX_BODY_BYTES: int = 1 * 1024 * 1024

# Routes that receive a request body and must be protected.  The check is an
# exact-prefix match so ``/api/investigations`` covers every sub-path too.
_GUARDED_PREFIXES: tuple[str, ...] = (
    "/alerts",
    "/investigate",
    "/api/investigations",
)

_TOO_LARGE_BODY: bytes = b'{"error":"payload too large"}'
_TOO_LARGE_STATUS: int = HTTPStatus.REQUEST_ENTITY_TOO_LARGE.value


def _is_guarded(path: str) -> bool:
    """Return True when *path* is one of the size-limited POST routes."""
    return any(path == p or path.startswith(p + "/") for p in _GUARDED_PREFIXES)


class BodySizeLimitMiddleware:
    """ASGI middleware: stream-count request bytes; abort with 413 at cap.

    Wraps both ``receive`` and ``send`` in a single pass so:

    * Every request chunk is counted before FastAPI sees it.
    * The inner app's response headers are held back until we know whether the
      body was oversized.  If it was, those headers are discarded and we write
      our own ``413 {"error": "payload too large"}`` to the real transport.
    * If the body is within the limit, the buffered headers are forwarded and
      all subsequent ``send`` calls pass through normally.

    This means Content-Length: 0 combined with a multi-MiB streamed body is
    correctly rejected, as is a missing Content-Length header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_guarded(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        total_bytes = 0
        limit_exceeded = False

        # ------------------------------------------------------------------ #
        # Wrapped receive: count bytes, signal EOF once cap is hit            #
        # ------------------------------------------------------------------ #

        async def _counting_receive() -> Message:
            nonlocal total_bytes, limit_exceeded

            if limit_exceeded:
                # Return a finished-body message so the inner app stops asking.
                return {"type": "http.request", "body": b"", "more_body": False}

            message: Message = await receive()
            if message.get("type") == "http.request":
                chunk: bytes = message.get("body", b"")
                total_bytes += len(chunk)
                if total_bytes > MAX_BODY_BYTES:
                    limit_exceeded = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
            return message

        # ------------------------------------------------------------------ #
        # Wrapped send: buffer the response-start message; decide after body  #
        # ------------------------------------------------------------------ #

        # We hold the inner app's http.response.start here until we know
        # whether the body was oversized.
        buffered_start: Message | None = None
        start_forwarded = False

        async def _gating_send(message: Message) -> None:
            nonlocal buffered_start, start_forwarded

            msg_type: str = message.get("type", "")

            if msg_type == "http.response.start":
                if limit_exceeded:
                    # Discard the inner app's start; we will write 413 below.
                    return
                # Hold the headers; forward them on the next body chunk.
                buffered_start = message
                return

            if msg_type == "http.response.body":
                if limit_exceeded:
                    # Also discard body chunks from the inner app.
                    return
                # Flush buffered start (once) before the first body chunk.
                if buffered_start is not None and not start_forwarded:
                    start_forwarded = True
                    await send(buffered_start)
                    buffered_start = None
                await send(message)
                return

            # Any other message type (e.g. websocket frames) passes through.
            await send(message)

        await self._app(scope, _counting_receive, _gating_send)

        if not limit_exceeded:
            return

        # The inner app's response was suppressed; write the authoritative 413.
        await send(
            {
                "type": "http.response.start",
                "status": _TOO_LARGE_STATUS,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": _TOO_LARGE_BODY,
                "more_body": False,
            }
        )
