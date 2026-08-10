"""Two ways the detached-investigation plumbing loses data without failing loudly.

* **The store must be one object per process.** ``PostgresInvestigationStore.__init__``
  runs the schema DDL and lazily opens a pool of up to ten server connections it never
  closes, so building one per launch exhausts ``max_connections`` after a handful of
  chat investigations. The in-memory store fails the other way: a second instance means
  the record one caller wrote is invisible to the worker draining the other.
* **The fallback thread must keep the caller's storage scope.** A fresh thread starts
  with an empty context, so an investigation launched from a scoped Slack turn would
  resolve its integrations unbound — the same defect as the fleet-search ``Context``
  bug, and it reads as "the agent cannot see any integrations", not as a wiring error.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from gateway.core.chat.delivery_target import ChatDeliveryTarget
from gateway.core.investigations import detached_launcher, storage_utils
from gateway.core.storage.investigations.store import InMemoryInvestigationStore

_ORG = "org_store_sharing"
_PLATFORM = "test-platform"


@pytest.fixture(autouse=True)
def _fresh_store() -> Any:
    storage_utils.reset_investigation_store_for_tests()
    yield
    storage_utils.reset_investigation_store_for_tests()


class _SilentNotifier:
    """Satisfies the launcher's "can this be delivered?" check and says nothing."""

    def post_ack(self, target: Any, ack: Any) -> str | None:
        _ = (target, ack)
        return None

    def update_stage(self, target: Any, stage: str, investigation_id: str) -> None:
        _ = (target, stage, investigation_id)

    def deliver_final(self, target: Any, report: str, investigation_id: str) -> bool:
        _ = (target, report, investigation_id)
        return True

    def report_failure(self, target: Any, error_summary: str, investigation_id: str) -> None:
        _ = (target, error_summary, investigation_id)


@pytest.fixture
def routable_turn(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A turn the launcher will accept: a bound target and a notifier that can reach it.

    Both are required — the launcher refuses rather than queue an investigation
    whose report has nowhere to land — and both are process-global or contextvar
    state, so this undoes itself.
    """
    from config.constants import datastore
    from gateway.core.chat import (
        bound_delivery_target,
        get_chat_notifier_registry,
        reset_chat_notifier_registry_for_tests,
    )

    # The launcher reads the DSN through a function-local import, so the module
    # attribute is the durable patch point.
    monkeypatch.setattr(datastore, "database_dsn", lambda: None)
    reset_chat_notifier_registry_for_tests()
    get_chat_notifier_registry().register(_PLATFORM, _SilentNotifier())
    with bound_delivery_target(_target()):
        yield
    reset_chat_notifier_registry_for_tests()


def _scope() -> StorageScope:
    return StorageScope(principal=Principal.org(_ORG), actor=Actor(id="U_SCOPE"))


def _target() -> ChatDeliveryTarget:
    return ChatDeliveryTarget(
        platform=_PLATFORM,
        channel_id="C_SCOPE",
        thread_ts="1700000000.000900",
        user_id="U_SCOPE",
    )


class _CountingPostgresStore(InMemoryInvestigationStore):
    """Stands in for the real Postgres store and counts how often it is constructed."""

    constructions = 0

    def __init__(self, dsn: str) -> None:
        super().__init__()
        self.dsn = dsn
        type(self).constructions += 1


@pytest.fixture
def counting_postgres(monkeypatch: pytest.MonkeyPatch) -> type[_CountingPostgresStore]:
    _CountingPostgresStore.constructions = 0
    monkeypatch.setattr(
        "gateway.core.storage.investigations.postgres.PostgresInvestigationStore",
        _CountingPostgresStore,
    )
    monkeypatch.setattr(storage_utils, "database_dsn", lambda: "postgresql://stub/db")
    return _CountingPostgresStore


def test_postgres_store_is_built_once_for_the_whole_process(
    counting_postgres: type[_CountingPostgresStore],
) -> None:
    """Every launch building its own store would open a fresh connection pool each time."""
    first = storage_utils.get_investigation_store()
    for _ in range(9):
        storage_utils.get_investigation_store()

    assert counting_postgres.constructions == 1
    assert storage_utils.get_investigation_store() is first


def test_web_routes_and_the_chat_launcher_share_one_store(
    counting_postgres: type[_CountingPostgresStore],
) -> None:
    """A record the chat launcher writes must be visible to the REST reader, and vice versa."""
    from gateway.web.investigations import _store as web_store

    assert web_store() is detached_launcher._get_store()
    assert counting_postgres.constructions == 1


def test_concurrent_first_callers_still_get_one_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unlocked check-then-set hands two simultaneous turns two different stores."""
    monkeypatch.setattr(storage_utils, "database_dsn", lambda: None)

    workers = 8
    ready = threading.Barrier(workers)
    seen: list[Any] = []
    lock = threading.Lock()

    def _fetch() -> None:
        ready.wait(timeout=10.0)
        store = storage_utils.get_investigation_store()
        with lock:
            seen.append(store)

    threads = [threading.Thread(target=_fetch, name=f"store-race-{i}") for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)
        assert not thread.is_alive()

    assert len(seen) == workers
    assert len({id(store) for store in seen}) == 1


def test_fallback_thread_runs_under_the_launching_turns_scope(
    monkeypatch: pytest.MonkeyPatch, routable_turn: Any
) -> None:
    """Without a context copy the detached run resolves integrations as nobody."""
    _ = routable_turn
    monkeypatch.setattr(storage_utils, "database_dsn", lambda: None)

    observed: list[StorageScope | None] = []
    ran = threading.Event()

    def _record_scope(_store: Any, _investigation_id: str, _target: Any) -> None:
        observed.append(current_scope())
        ran.set()

    monkeypatch.setattr(detached_launcher, "_run_investigation_background", _record_scope)

    scope = _scope()
    with bound_storage_scope(scope):
        detached_launcher.launch_detached_investigation("why is checkout slow")

    assert ran.wait(10.0), "the fallback never started a background run"
    assert observed == [scope]


def test_record_is_owned_by_the_org_that_asked(
    monkeypatch: pytest.MonkeyPatch, routable_turn: Any
) -> None:
    """An unattributed record cannot be polled or audited by the org that triggered it.

    Both halves of the scope have to survive the trip. The worker rebuilds it from
    the stored trigger — nothing else reaches that thread — and falls back to a
    literal ``"worker"`` actor, so an actor id that never got written looks exactly
    like a run nobody asked for.
    """
    _ = routable_turn
    monkeypatch.setattr(storage_utils, "database_dsn", lambda: None)
    monkeypatch.setattr(detached_launcher, "_run_investigation_background", lambda *_a: None)

    with bound_storage_scope(_scope()):
        result = detached_launcher.launch_detached_investigation("check the api")

    record = storage_utils.get_investigation_store().get(result.investigation_id)
    assert record is not None
    assert record.clerk_org_id == Principal.org(_ORG).id
    assert record.trigger["scope"] == {"org_id": _ORG, "actor_id": "U_SCOPE"}
