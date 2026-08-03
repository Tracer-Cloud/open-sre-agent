"""Tests for organization-scoped durable API run execution."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gateway.runtime.concurrency import TurnConcurrencyGate
from gateway.runtime.remote_run_worker import RemoteRunWorker, build_remote_run_worker
from platform.deployment_contracts.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
)


def _run() -> AgentRun:
    now = datetime.now(UTC)
    return AgentRun(
        id="run-1",
        organization_id="org-a",
        source=AgentRunSource.API,
        prompt="diagnose",
        status=AgentRunStatus.QUEUED,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )


class _Repository:
    def __init__(self) -> None:
        self.queued: AgentRun | None = _run()
        self.claims: list[str] = []
        self.renewals = 0
        self.finished: list[dict[str, Any]] = []
        self.done = threading.Event()

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        _ = (worker_id, lease_duration)
        self.claims.append(organization_id)
        claimed, self.queued = self.queued, None
        return claimed

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        _ = (run_id, worker_id, lease_duration)
        self.renewals += 1
        return True

    def finalize_owned_agent_run(self, **values: Any) -> bool:
        self.finished.append(values)
        self.done.set()
        return True


class _Resolver:
    def resolve(self, *, user_id: str, chat_id: str) -> Any:
        assert (user_id, chat_id) == ("org-a", "run-1")
        return type("Session", (), {"session_id": "session-api"})()


def test_api_run_stays_queued_while_chat_owns_shared_capacity() -> None:
    repository = _Repository()
    gate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True  # active chat turn

    def handler(
        text: str,
        _session: object,
        sink: Any,
        _logger: logging.Logger,
    ) -> None:
        assert text == "diagnose"
        sink.finalize("completed")

    worker = RemoteRunWorker(
        organization_id="org-a",
        repository=repository,  # type: ignore[arg-type]
        handler=handler,  # type: ignore[arg-type]
        session_resolver=_Resolver(),  # type: ignore[arg-type]
        gate=gate,
        logger=logging.getLogger("test"),
        poll_interval_seconds=0.01,
    )
    worker.start()
    time.sleep(0.05)

    assert repository.claims == []
    gate.release()
    assert repository.done.wait(1)
    assert worker.stop(timeout=1)

    assert repository.claims[0] == "org-a"
    assert repository.finished == [
        {
            "run_id": "run-1",
            "worker_id": worker._worker_id,
            "status": AgentRunStatus.SUCCEEDED,
            "result": {"output": "completed", "session_id": "session-api"},
        }
    ]


def test_long_run_renews_lease_and_failure_is_generic() -> None:
    repository = _Repository()

    def handler(*_args: object) -> None:
        time.sleep(0.08)
        raise RuntimeError("provider response with secret")

    worker = RemoteRunWorker(
        organization_id="org-a",
        repository=repository,  # type: ignore[arg-type]
        handler=handler,  # type: ignore[arg-type]
        session_resolver=_Resolver(),  # type: ignore[arg-type]
        gate=TurnConcurrencyGate(1),
        logger=logging.getLogger("test"),
        poll_interval_seconds=0.01,
        lease_duration=timedelta(seconds=0.06),
        worker_id="worker-a",
    )
    worker.start()
    assert repository.done.wait(1)
    assert worker.stop(timeout=1)

    assert repository.renewals >= 1
    assert repository.finished[0] == {
        "run_id": "run-1",
        "worker_id": "worker-a",
        "status": AgentRunStatus.FAILED,
        "result": {"output": "The remote agent run failed."},
        "error_code": "RuntimeError",
    }


def test_factory_resolves_bindings_despite_legacy_state_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory must use the bindings database, not the install catalog.

    Persistent volumes deployed before the bindings/installs split carry a
    ``state.db`` whose ``gateway_session_bindings`` table predates the
    ``principal_id`` column. Wiring the resolver to that database made every
    API run fail with ``sqlite3.OperationalError`` in production.
    """
    import gateway.storage.db as db_module

    monkeypatch.setattr(db_module, "host_home", lambda: tmp_path / "host")
    monkeypatch.setattr(db_module, "opensre_home", lambda: tmp_path / "host")
    legacy_dir = tmp_path / "host" / "gateway"
    legacy_dir.mkdir(parents=True)
    legacy = sqlite3.connect(legacy_dir / "state.db")
    legacy.execute(
        "CREATE TABLE gateway_session_bindings ("
        "platform TEXT NOT NULL, chat_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, updated_at REAL NOT NULL, "
        "PRIMARY KEY (platform, chat_id))"
    )
    legacy.execute(
        "INSERT INTO gateway_session_bindings VALUES ('api', 'run-0', 'legacy-session', 1.0)"
    )
    legacy.commit()
    legacy.close()

    # The repository is not exercised here; the test is about which bindings
    # database the resolver opens, so a constructor that touches no network is
    # all the worker needs.
    class _StubRepository:
        def __init__(self, dsn: str) -> None:
            _ = dsn

    monkeypatch.setattr(
        "gateway.storage.agent_runs.postgres.PostgresAgentRunRepository", _StubRepository
    )

    worker = build_remote_run_worker(
        organization_id="org-a",
        database_url="postgresql://unused.example/db",
        handler=lambda *_args: None,  # type: ignore[arg-type, misc]
        gate=TurnConcurrencyGate(1),
        logger=logging.getLogger("test"),
    )

    bindings = worker._session_resolver._bindings
    # The exact lookup the production run path performs; against the install
    # catalog this raised sqlite3.OperationalError (no such column).
    assert bindings.get_session_id(platform="api", chat_id="run-1") is None
    # Pre-split rows must be adopted, not dropped.
    assert bindings.get_session_id(platform="api", chat_id="run-0") == "legacy-session"
