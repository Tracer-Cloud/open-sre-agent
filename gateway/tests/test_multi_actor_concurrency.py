"""Concurrency: many teammates on one org must not corrupt shared indexes or mix sessions.

The safety claim for silo Slack is:

* **Shared** — one ``bindings.json`` (and one ``integrations.json``) per org.
* **Private** — each actor's session JSONL under ``users/<U>/sessions/``.
* **Compute** — fresh agent per turn; several turns may run on threads at once.

These tests hammer the shared binding document and per-actor session writers
the way a busy gateway does (several ``ThreadPoolExecutor`` workers), and check
for lost rows, unreadable JSON, and cross-actor session bleed.
"""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from core.agent_harness.session.persistence.paths import session_path, sessions_dir
from gateway.core.storage.session.file_bindings import FileBindingStore

ACME = Principal.org("org_acme")
ALICE = "U_ALICE"
BOB = "U_BOB"
_PLATFORM = "slack"
_THREAD = "C_SHARED:1.0"


class _SessionStub:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.started_at = time.time()


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "host"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", root)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)
    return root


@pytest.fixture
def bindings_path(host: Path) -> Path:
    path = host / "orgs" / ACME.id / "gateway" / "bindings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _scope(actor_id: str) -> StorageScope:
    return StorageScope(principal=ACME, actor=Actor(id=actor_id))


def _join(threads: list[threading.Thread], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"threads still running: {alive}"


def _read_bindings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parseable_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        assert isinstance(rec, dict), f"non-object JSONL line in {path}: {line!r}"
        rows.append(rec)
    return rows


# ── Shared bindings.json ────────────────────────────────────────────────────


def test_concurrent_alice_bob_bind_same_thread_keeps_both_rows(bindings_path: Path) -> None:
    """Two actors binding the same Slack thread at once must both survive."""
    store = FileBindingStore(bindings_path)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(actor: str, session_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.bind(
                platform=_PLATFORM,
                chat_id=_THREAD,
                session_id=session_id,
                principal=ACME,
                actor=actor,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(ALICE, "sess-alice"), name="alice"),
        threading.Thread(target=worker, args=(BOB, "sess-bob"), name="bob"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert (
        store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
        == "sess-alice"
    )
    assert (
        store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=BOB)
        == "sess-bob"
    )
    data = _read_bindings(bindings_path)
    assert isinstance(data.get("bindings"), list)
    keys = {
        (r.get("principal_id"), r.get("actor_id"), r.get("session_id"))
        for r in data["bindings"]
        if isinstance(r, dict)
    }
    assert (ACME.id, ALICE, "sess-alice") in keys
    assert (ACME.id, BOB, "sess-bob") in keys


def test_concurrent_many_actors_all_bindings_survive(bindings_path: Path) -> None:
    """Fan-out like a busy channel: N actors, one shared index, no lost rows."""
    store = FileBindingStore(bindings_path)
    n = 12
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        actor = f"U_{idx:03d}"
        try:
            barrier.wait(timeout=5)
            store.bind(
                platform=_PLATFORM,
                chat_id=_THREAD,
                session_id=f"sess-{idx}",
                principal=ACME,
                actor=actor,
            )
            # Interleave reads the way status / has_any_actor_binding does.
            assert store.has_any_actor_binding(platform=_PLATFORM, chat_id=_THREAD, principal=ACME)
            got = store.get_session_id(
                platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=actor
            )
            assert got == f"sess-{idx}"
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), name=f"u{i}") for i in range(n)]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    data = _read_bindings(bindings_path)
    actors = {
        r.get("actor_id")
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("chat_id") == _THREAD
    }
    assert actors == {f"U_{i:03d}" for i in range(n)}


def test_concurrent_rotate_same_actor_leaves_valid_document(bindings_path: Path) -> None:
    """Contended writes for one key must not produce unreadable JSON."""
    store = FileBindingStore(bindings_path)
    store.bind(
        platform=_PLATFORM,
        chat_id=_THREAD,
        session_id="seed",
        principal=ACME,
        actor=ALICE,
    )
    n = 8
    barrier = threading.Barrier(n)
    errors: list[Exception] = []
    winners: list[str] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            new_id = store.rotate(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
            winners.append(new_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, name=f"rot{i}") for i in range(n)]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    data = _read_bindings(bindings_path)  # must parse
    alice_rows = [
        r
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("actor_id") == ALICE and r.get("chat_id") == _THREAD
    ]
    assert len(alice_rows) == 1
    final = store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
    assert final in winners
    assert final == alice_rows[0]["session_id"]


def _mp_bind_worker(bindings: str, actor: str, session_id: str) -> None:
    store = FileBindingStore(Path(bindings))
    store.bind(
        platform=_PLATFORM,
        chat_id=_THREAD,
        session_id=session_id,
        principal=ACME,
        actor=actor,
    )


def test_multiprocess_alice_bob_bind_no_corrupt_json(bindings_path: Path) -> None:
    """Two OS processes (closer to two writers) must leave a valid document."""
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_bind_worker, args=(str(bindings_path), ALICE, "sess-alice-mp")),
        ctx.Process(target=_mp_bind_worker, args=(str(bindings_path), BOB, "sess-bob-mp")),
    ]
    for p in procs:
        p.start()
    deadline = time.monotonic() + 30
    for p in procs:
        p.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [p.pid for p in procs if p.is_alive()]
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(5)
    assert not alive, f"processes hung: {alive}"
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    data = _read_bindings(bindings_path)
    by_actor = {
        r["actor_id"]: r["session_id"]
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("chat_id") == _THREAD
    }
    assert by_actor[ALICE] == "sess-alice-mp"
    assert by_actor[BOB] == "sess-bob-mp"


# ── Per-actor session JSONL ─────────────────────────────────────────────────


@pytest.mark.usefixtures("host")
def test_concurrent_alice_bob_session_appends_do_not_mix() -> None:
    """Scoped writers append into different trees; neither sees the other's lines."""
    storage = JsonlSessionStorage()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []
    paths_seen: dict[str, str] = {}

    def worker(actor: str, session_id: str, marker: str) -> None:
        try:
            with bound_storage_scope(_scope(actor)):
                barrier.wait(timeout=5)
                stub = _SessionStub(session_id)
                storage.open_session(stub)
                for i in range(20):
                    storage.append_message(
                        session_id,
                        role="user",
                        content=f"{marker}-{i}",
                        metadata={"kind": "chat", "actor": actor},
                    )
                path = session_path(session_id)
                paths_seen[actor] = str(path)
                assert actor in str(path)
                rows = _parseable_jsonl(path)
                texts = [r.get("content") for r in rows if r.get("type") == "message"]
                assert all(isinstance(t, str) and t.startswith(marker) for t in texts)
                assert len(texts) == 20
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(ALICE, "alice-sess", "alice"), name="alice"),
        threading.Thread(target=worker, args=(BOB, "bob-sess", "bob"), name="bob"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert paths_seen[ALICE] != paths_seen[BOB]
    alice_text = Path(paths_seen[ALICE]).read_text(encoding="utf-8")
    bob_text = Path(paths_seen[BOB]).read_text(encoding="utf-8")
    assert "alice-" in alice_text and "bob-" not in alice_text
    assert "bob-" in bob_text and "alice-" not in bob_text
    # Org integrations root stays shared; session homes stay private.
    with bound_storage_scope(_scope(ALICE)):
        alice_sessions = sessions_dir()
    with bound_storage_scope(_scope(BOB)):
        bob_sessions = sessions_dir()
    assert alice_sessions != bob_sessions
    assert ALICE in str(alice_sessions)
    assert BOB in str(bob_sessions)


@pytest.mark.usefixtures("host")
def test_same_session_concurrent_appends_remain_line_parseable() -> None:
    """If two threads append the same file (lock skipped), lines must stay JSON.

    Production serializes per conversation, so this should not happen for one
    actor. The test pins whether the append-only writer is still safe under
    accidental double-entry — a regression tripwire for data corruption.
    """
    storage = JsonlSessionStorage()
    session_id = "shared-sess"
    with bound_storage_scope(_scope(ALICE)):
        storage.open_session(_SessionStub(session_id))
        path = session_path(session_id)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(tag: str) -> None:
        try:
            with bound_storage_scope(_scope(ALICE)):
                barrier.wait(timeout=5)
                for i in range(40):
                    storage.append_message(
                        session_id,
                        role="assistant",
                        content=f"{tag}-{i}",
                        metadata={"kind": "chat"},
                    )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("a",), name="a"),
        threading.Thread(target=worker, args=("b",), name="b"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    rows = _parseable_jsonl(path)
    messages = [r for r in rows if r.get("type") == "message"]
    assert len(messages) == 80
    contents = {r.get("content") for r in messages}
    assert {f"a-{i}" for i in range(40)} | {f"b-{i}" for i in range(40)} <= contents
