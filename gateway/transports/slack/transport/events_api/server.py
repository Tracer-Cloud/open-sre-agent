"""Slack's own HTTP listener for Events API inbound.

A dedicated app on its own port, not a route added to :mod:`gateway.web`: that
app is the health and alert surface, and the package DAG forbids it importing a
chat transport. Keeping the listener here also keeps the two inbound transports
symmetrical — Socket Mode owns its connection in :mod:`.worker`, Events API
owns its server here.

Slack retries any delivery it does not see answered within three seconds, so
both routes verify, hand the message to ``handler`` on a worker thread, and
return immediately. The turn never runs inside the request.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from gateway.core.runtime.approvals import ApprovalBroker
from gateway.core.runtime.errors import GatewayTransportFailedError
from gateway.transports.slack.delivery.approvals import handle_block_actions_payload
from gateway.transports.slack.delivery.feedback import record_feedback_payload
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.settings import SlackGatewaySettings
from gateway.transports.slack.transport.events_api.receiver import (
    SlackEventDeduplicator,
    SlackHttpStatus,
    admit_slack_http_request,
    admit_slack_interactivity_request,
)

#: Starts a turn for one inbound message without blocking the request.
SubmitTurn = Callable[[SlackInboundMessage], None]

EVENTS_PATH = "/slack/events"
# Slack posts button clicks to a separate request URL. Without this route
# approvals and feedback silently stop working while turns still run.
INTERACTIVITY_PATH = "/slack/interactivity"

# How long a failed start waits for the listener thread to unwind before
# giving up on it; the thread is a daemon, so it cannot block exit.
_FAILED_START_JOIN_SECONDS = 5.0

logger = logging.getLogger("gateway")


@dataclass
class SlackHttpServerHandle:
    """A running Slack HTTP listener."""

    server: uvicorn.Server
    thread: threading.Thread
    workers: ThreadPoolExecutor
    bound_host: str
    bound_port: int

    @property
    def bound_address(self) -> str:
        return f"{self.bound_host}:{self.bound_port}"

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Stop serving and report whether the listener thread ended in time.

        Signature matches ``gateway.channels.chat.TransportWorker`` so the
        composition root can hold either inbound transport in one handle.
        """
        self.server.should_exit = True
        self.thread.join(timeout=timeout)
        self.workers.shutdown(wait=False, cancel_futures=True)
        return not self.thread.is_alive()


def build_slack_http_app(
    *,
    settings: SlackGatewaySettings,
    approvals: ApprovalBroker,
    deduplicator: SlackEventDeduplicator,
    submit_turn: SubmitTurn,
) -> FastAPI:
    """Return the Slack listener app.

    ``submit_turn`` takes one :class:`SlackInboundMessage` and starts the turn
    without blocking — the route must answer Slack first.
    """
    app = FastAPI()

    async def _admit(request: Request) -> Response:
        body = await request.body()  # raw bytes: the signature covers them exactly
        outcome = admit_slack_http_request(
            headers=dict(request.headers),
            body=body,
            signing_secret=settings.signing_secret,
            deduplicator=deduplicator,
        )
        if outcome.status is SlackHttpStatus.REJECTED:
            # Detail stays server-side (CWE-209): the caller is unauthenticated.
            logger.warning("slack http rejected: %s", outcome.reason)
            return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
        if outcome.status is SlackHttpStatus.CHALLENGE:
            return PlainTextResponse(outcome.challenge)
        if outcome.status is SlackHttpStatus.ACCEPTED and outcome.message is not None:
            try:
                submit_turn(outcome.message)
            except RuntimeError:
                # The turn executor is gone: the listener outlived a failed
                # start or a stop. Release the claim first — admission already
                # recorded it, and a retry refused as a duplicate would drop the
                # event entirely. Then 503 so Slack retries against a healthy
                # replica rather than 500 on every delivery.
                released = deduplicator.release(outcome.event_id)
                logger.warning("slack http accepted an event with no turn executor")
                if not released:
                    # The claim survived, so Slack's retry may be refused as a
                    # duplicate and the event lost. 500 rather than 503: this is
                    # not a clean "try again", and it must page as a server fault.
                    logger.error(
                        "slack http could not release event %s; retry may be dropped",
                        outcome.event_id,
                    )
                    return JSONResponse(
                        {"error": "internal error"},
                        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return JSONResponse(
                    {"error": "unavailable"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE
                )
        return JSONResponse({"ok": True})

    async def _interactivity(request: Request) -> Response:
        body = await request.body()
        outcome = admit_slack_interactivity_request(
            headers=dict(request.headers),
            body=body,
            signing_secret=settings.signing_secret,
        )
        if outcome.status is SlackHttpStatus.REJECTED:
            logger.warning("slack interactivity rejected: %s", outcome.reason)
            return JSONResponse({"error": "unauthorized"}, status_code=HTTPStatus.UNAUTHORIZED)
        if outcome.payload is not None:
            # Resolved on the request thread, never the turn executor: workers
            # may all be blocked *waiting* on these very buttons, so a click
            # must never need a free worker to be recorded.
            record_feedback_payload(outcome.payload)
            handle_block_actions_payload(
                outcome.payload,
                broker=approvals,
                allowed_user_ids=settings.allowed_user_ids,
                allow_open_workspace=settings.allow_open_workspace,
            )
        return JSONResponse({"ok": True})

    app.add_api_route(EVENTS_PATH, _admit, methods=["POST"])
    app.add_api_route(INTERACTIVITY_PATH, _interactivity, methods=["POST"])
    return app


def serve_slack_http_in_thread(
    *,
    app: FastAPI,
    host: str = "0.0.0.0",
    port: int,
    workers: ThreadPoolExecutor,
    startup_timeout: float = 10.0,
) -> SlackHttpServerHandle:
    """Start uvicorn on ``app`` and wait until it is bound."""
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None))
    thread = threading.Thread(target=server.run, name="slack-http", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if server.started and server.servers:
            bound = server.servers[0].sockets[0].getsockname()
            return SlackHttpServerHandle(server, thread, workers, str(bound[0]), int(bound[1]))
        if not thread.is_alive():
            break
        time.sleep(0.05)
    # Stop the listener before the executor. A bind that completes just after
    # the deadline would otherwise keep serving Slack with no worker to run the
    # turn — every request 500s and Slack retries it, while the gateway has
    # already recorded Slack as unavailable.
    server.should_exit = True
    thread.join(timeout=_FAILED_START_JOIN_SECONDS)
    if thread.is_alive():
        # Bounded wait, not an unbounded one: a listener that will not unwind
        # must not hold up the rest of the gateway. It is a daemon thread, so
        # it cannot outlive the process, and any delivery it still answers gets
        # a 503 from the executor guard above rather than a 500.
        logger.warning(
            "slack http listener did not stop within %ss after a failed start",
            _FAILED_START_JOIN_SECONDS,
        )
    workers.shutdown(wait=False, cancel_futures=True)
    # GatewayTransportFailedError, not RuntimeError: start_transports catches
    # this and records Slack unavailable instead of aborting the gateway.
    raise GatewayTransportFailedError(f"Slack HTTP listener on {host}:{port} failed to start")


__all__ = [
    "EVENTS_PATH",
    "INTERACTIVITY_PATH",
    "SlackHttpServerHandle",
    "build_slack_http_app",
    "serve_slack_http_in_thread",
]
