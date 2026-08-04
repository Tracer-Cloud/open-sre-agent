"""Organization-scoped durable API-run worker for the Fargate Gateway."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterable
from datetime import timedelta

from gateway.runtime.concurrency import TurnConcurrencyGate
from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.storage import SessionResolver
from gateway.storage.session.binding_store import open_binding_store
from platform.deployment_contracts.models import AgentRun, AgentRunStatus
from platform.deployment_contracts.ports import AgentRunRepository

_GENERIC_FAILURE = "The remote agent run failed."


class DatabaseGatewaySink:
    """Collect one API turn and persist its terminal result through the run repository."""

    def __init__(
        self,
        *,
        repository: AgentRunRepository,
        run_id: str,
        worker_id: str,
    ) -> None:
        self._repository = repository
        self._run_id = run_id
        self._worker_id = worker_id
        self._final_text = ""

    @property
    def final_text(self) -> str:
        """Return the non-sensitive assistant output accumulated for this run."""
        return self._final_text

    def print(self, message: str = "") -> None:
        if message:
            self._final_text = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        # The shared engine may pass provider exception details here. Never
        # persist those details to the externally readable run result.
        _ = message
        self._final_text = _GENERIC_FAILURE

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(str(chunk) for chunk in chunks)
        self._final_text = text
        return text

    def set_tool_status(self, text: str) -> None:
        _ = text

    def finalize(self, text: str) -> None:
        self._final_text = text

    def persist_success(self, *, session_id: str) -> bool:
        """Persist a safe successful result while this worker owns the lease."""
        return self._repository.finalize_owned_agent_run(
            run_id=self._run_id,
            worker_id=self._worker_id,
            status=AgentRunStatus.SUCCEEDED,
            result={
                "output": self._final_text,
                "session_id": session_id,
            },
        )

    def persist_failure(self, *, error_code: str) -> bool:
        """Persist only a stable exception class, never exception detail."""
        return self._repository.finalize_owned_agent_run(
            run_id=self._run_id,
            worker_id=self._worker_id,
            status=AgentRunStatus.FAILED,
            result={"output": _GENERIC_FAILURE},
            error_code=error_code,
        )


class _LeaseRenewer:
    """Renew a run lease until its turn finishes or ownership is lost."""

    def __init__(
        self,
        *,
        repository: AgentRunRepository,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
        logger: logging.Logger,
    ) -> None:
        self._repository = repository
        self._run_id = run_id
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._logger = logger
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"gateway-lease-{run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        interval = max(0.05, self._lease_duration.total_seconds() / 3)
        while not self._stop.wait(interval):
            try:
                renewed = self._repository.extend_owned_agent_run_lease(
                    run_id=self._run_id,
                    worker_id=self._worker_id,
                    lease_duration=self._lease_duration,
                )
            except Exception as exc:
                self._logger.warning("remote run lease renewal failed (%s)", type(exc).__name__)
                return
            if not renewed:
                return


class RemoteRunWorker:
    """Poll, claim, and execute durable runs for exactly one organization."""

    def __init__(
        self,
        *,
        organization_id: str,
        repository: AgentRunRepository,
        handler: GatewayAgentCallback,
        session_resolver: SessionResolver,
        gate: TurnConcurrencyGate,
        logger: logging.Logger,
        worker_id: str | None = None,
        poll_interval_seconds: float = 1.0,
        lease_duration: timedelta = timedelta(minutes=2),
    ) -> None:
        if not organization_id.strip():
            raise ValueError("organization_id must not be blank")
        if poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease duration must be positive")
        self._organization_id = organization_id
        self._repository = repository
        self._handler = handler
        self._session_resolver = session_resolver
        self._gate = gate
        self._logger = logger
        self._worker_id = worker_id or f"gateway-{uuid.uuid4()}"
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = lease_duration
        self._stop = threading.Event()
        self._poller = threading.Thread(
            target=self._poll,
            name="gateway-remote-run-poller",
            daemon=True,
        )
        self._active_lock = threading.Lock()
        self._active: set[threading.Thread] = set()

    def start(self) -> None:
        """Begin polling in the background."""
        self._poller.start()

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Stop claiming new work and wait boundedly for active turns."""
        self._stop.set()
        self._poller.join(timeout=timeout)
        with self._active_lock:
            threads = tuple(self._active)
        for thread in threads:
            thread.join(timeout=max(0.0, timeout))
        return not self._poller.is_alive() and all(not thread.is_alive() for thread in threads)

    def _poll(self) -> None:
        while not self._stop.is_set():
            if not self._gate.try_acquire():
                self._stop.wait(self._poll_interval_seconds)
                continue
            try:
                run = self._repository.claim_oldest_available_agent_run(
                    organization_id=self._organization_id,
                    worker_id=self._worker_id,
                    lease_duration=self._lease_duration,
                )
            except Exception as exc:
                self._gate.release()
                self._logger.warning(
                    "remote run claim failed (%s)",
                    type(exc).__name__,
                )
                self._stop.wait(self._poll_interval_seconds)
                continue
            if run is None:
                self._gate.release()
                self._stop.wait(self._poll_interval_seconds)
                continue
            thread = threading.Thread(
                target=self._execute,
                args=(run,),
                name=f"gateway-run-{run.id}",
                daemon=True,
            )
            with self._active_lock:
                self._active.add(thread)
            thread.start()

    def _execute(self, run: AgentRun) -> None:
        sink = DatabaseGatewaySink(
            repository=self._repository,
            run_id=run.id,
            worker_id=self._worker_id,
        )
        renewer = _LeaseRenewer(
            repository=self._repository,
            run_id=run.id,
            worker_id=self._worker_id,
            lease_duration=self._lease_duration,
            logger=self._logger,
        )
        renewer.start()
        try:
            session = self._session_resolver.resolve(
                user_id=run.organization_id,
                chat_id=run.id,
            )
            self._handler(run.prompt, session, sink, self._logger)
            if not sink.persist_success(session_id=session.session_id):
                self._logger.warning("remote run completion lost its lease")
        except Exception as exc:
            self._logger.warning("remote run failed (%s)", type(exc).__name__)
            try:
                if not sink.persist_failure(error_code=type(exc).__name__):
                    self._logger.warning("remote run failure lost its lease")
            except Exception as persistence_exc:
                self._logger.warning(
                    "remote run failure persistence failed (%s)",
                    type(persistence_exc).__name__,
                )
        finally:
            renewer.stop()
            self._gate.release()
            current = threading.current_thread()
            with self._active_lock:
                self._active.discard(current)


def build_remote_run_worker(
    *,
    organization_id: str,
    database_url: str,
    handler: GatewayAgentCallback,
    gate: TurnConcurrencyGate,
    logger: logging.Logger,
) -> RemoteRunWorker:
    """Compose the production Neon repository and API session resolver."""
    from gateway.storage.agent_runs.postgres import PostgresAgentRunRepository

    repository = PostgresAgentRunRepository(database_url)
    # The bindings database (not the host install catalog): it carries the
    # bindings schema, migrates pre-principal rows, and follows the mounted
    # organization volume so replaced tasks keep their conversations.
    resolver = SessionResolver(open_binding_store(), platform="api")
    return RemoteRunWorker(
        organization_id=organization_id,
        repository=repository,
        handler=handler,
        session_resolver=resolver,
        gate=gate,
        logger=logger,
    )


__all__ = [
    "DatabaseGatewaySink",
    "RemoteRunWorker",
    "build_remote_run_worker",
]
