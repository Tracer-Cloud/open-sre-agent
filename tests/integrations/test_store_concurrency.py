"""Concurrency tests for the integration store.

These tests verify that concurrent writes do not lose updates, corrupt the
store, or leave orphaned temp files behind.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from filelock import Timeout

from app.integrations.store import (
    IntegrationStoreLockTimeout,
    _load_raw,
    _save,
    remove_integration,
    upsert_instance,
    upsert_integration,
)


@pytest.fixture
def tmp_store(tmp_path: Path):
    store_file = tmp_path / "integrations.json"
    with patch("app.integrations.store.STORE_PATH", store_file):
        yield store_file


def _seed(store_file: Path, records: list[dict]) -> None:
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text(
        json.dumps({"version": 2, "integrations": records}) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _join_threads(threads: list[threading.Thread], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [thread.name for thread in threads if thread.is_alive()]
    assert not alive, f"Threads did not finish within {timeout}s: {alive}"


def _join_processes(processes: list[multiprocessing.Process], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for process in processes:
        process.join(timeout=max(0.0, deadline - time.monotonic()))

    alive = [process.pid for process in processes if process.is_alive()]
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert not alive, f"Processes did not finish within {timeout}s: {alive}"


def test_concurrent_thread_upsert_distinct_services(tmp_store: Path) -> None:
    """Many threads writing distinct services concurrently must all survive."""
    num_threads = 10
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            upsert_integration(
                f"service-{idx}",
                {"credentials": {"token": f"t{idx}"}},
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    _join_threads(threads)

    assert not errors, f"Worker exceptions: {errors}"
    data = _read_json(tmp_store)
    services = {i["service"] for i in data.get("integrations", [])}
    assert services == {f"service-{i}" for i in range(num_threads)}


def test_concurrent_thread_upsert_same_service(tmp_store: Path) -> None:
    """Two threads updating the same service must leave consistent state."""
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [{"name": "prod", "tags": {}, "credentials": {"endpoint": "a"}}],
            }
        ],
    )

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker_a() -> None:
        try:
            barrier.wait(timeout=5)
            upsert_instance(
                "grafana",
                {"name": "prod", "tags": {}, "credentials": {"endpoint": "new-a"}},
                record_id="g1",
            )
        except Exception as exc:
            errors.append(exc)

    def worker_b() -> None:
        try:
            barrier.wait(timeout=5)
            upsert_instance(
                "grafana",
                {"name": "staging", "tags": {}, "credentials": {"endpoint": "new-b"}},
                record_id="g1",
            )
        except Exception as exc:
            errors.append(exc)

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()
    _join_threads([ta, tb])

    assert not errors, f"Worker exceptions: {errors}"
    data = _read_json(tmp_store)
    grafana_records = [i for i in data["integrations"] if i["service"] == "grafana"]
    assert len(grafana_records) == 1
    names = {inst["name"] for inst in grafana_records[0]["instances"]}
    assert names == {"prod", "staging"}


def _cross_process_worker(store_path_str: str, service: str, token: str) -> None:
    """Helper to run in a child process."""
    from pathlib import Path
    from unittest.mock import patch

    from app.integrations.store import upsert_integration

    store_file = Path(store_path_str)
    with patch("app.integrations.store.STORE_PATH", store_file):
        upsert_integration(service, {"credentials": {"token": token}})


def test_cross_process_writes(tmp_path: Path) -> None:
    """Multiple processes writing to the same store must not lose data."""
    store_file = tmp_path / "integrations.json"
    services = [f"proc-{i}" for i in range(4)]

    # We patch inside each child process, but the parent also needs to read
    # the result, so we use the real path for everything.
    processes = [
        multiprocessing.Process(
            target=_cross_process_worker,
            args=(str(store_file), svc, f"tok-{i}"),
        )
        for i, svc in enumerate(services)
    ]

    for p in processes:
        p.start()
    _join_processes(processes)

    for p in processes:
        assert p.exitcode == 0, f"Process exited with code {p.exitcode}"

    data = _read_json(store_file)
    stored_services = {i["service"] for i in data.get("integrations", [])}
    assert stored_services == set(services)


def test_atomic_write_failure_cleanup(tmp_store: Path) -> None:
    """If os.replace fails, the original file stays intact and temp files are cleaned."""
    _seed(tmp_store, [{"id": "x1", "service": "x", "status": "active", "instances": []}])

    original_text = tmp_store.read_text(encoding="utf-8")
    original_mtime = tmp_store.stat().st_mtime

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    with (
        patch("app.integrations.store.os.replace", side_effect=failing_replace),
        pytest.raises(OSError, match="simulated replace failure"),
    ):
        _save({"version": 2, "integrations": []})

    # Original file untouched
    assert tmp_store.read_text(encoding="utf-8") == original_text
    assert tmp_store.stat().st_mtime == original_mtime

    # No temp files left behind
    temps = list(tmp_store.parent.glob(tmp_store.name + ".tmp*"))
    assert not temps, f"Orphaned temp files: {temps}"


def test_v1_load_returns_migrated_data_when_persist_fails(tmp_store: Path) -> None:
    """Read-time v1 migration should not fail just because write-through failed."""
    v1_data = {
        "version": 1,
        "integrations": [
            {
                "id": "grafana-abc",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://example.com", "api_key": "k"},
            }
        ],
    }
    tmp_store.write_text(json.dumps(v1_data) + "\n", encoding="utf-8")

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    with patch("app.integrations.store.os.replace", side_effect=failing_replace):
        data = _load_raw()

    assert data["version"] == 2
    assert data["integrations"][0]["instances"][0]["credentials"]["api_key"] == "k"
    assert _read_json(tmp_store)["version"] == 1
    temps = list(tmp_store.parent.glob(tmp_store.name + ".tmp*"))
    assert not temps, f"Orphaned temp files: {temps}"


def test_v1_load_returns_migrated_data_when_migration_lock_times_out(tmp_store: Path) -> None:
    """Read-time v1 migration keeps working when the persist lock cannot be acquired."""

    class TimedOutLock:
        def __enter__(self) -> None:
            raise Timeout(str(tmp_store.with_suffix(".lock")))

        def __exit__(self, *_args: object) -> None:
            return None

    v1_data = {
        "version": 1,
        "integrations": [
            {
                "id": "grafana-abc",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://example.com", "api_key": "k"},
            }
        ],
    }
    tmp_store.write_text(json.dumps(v1_data) + "\n", encoding="utf-8")

    with patch("app.integrations.store._acquire_lock", return_value=TimedOutLock()):
        data = _load_raw()

    assert data["version"] == 2
    assert data["integrations"][0]["instances"][0]["credentials"]["api_key"] == "k"
    assert _read_json(tmp_store)["version"] == 1


def test_v1_migration_persists_when_locked_update_is_noop(tmp_store: Path) -> None:
    """A no-op remove should still persist an in-memory v1-to-v2 migration."""
    v1_data = {
        "version": 1,
        "integrations": [
            {
                "id": "grafana-abc",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://example.com", "api_key": "k"},
            }
        ],
    }
    tmp_store.write_text(json.dumps(v1_data) + "\n", encoding="utf-8")

    assert remove_integration("missing-service") is False

    data = _read_json(tmp_store)
    assert data["version"] == 2
    assert data["integrations"][0]["instances"][0]["credentials"]["api_key"] == "k"


def test_permissions_preserved_after_concurrent_replace(tmp_store: Path) -> None:
    """After concurrent writes, the store file must still have 0o600."""
    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            upsert_integration(f"svc-{idx}", {"credentials": {"k": str(idx)}})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    _join_threads(threads)

    assert not errors
    mode = stat.S_IMODE(tmp_store.stat().st_mode)
    if os.name == "nt":
        assert mode & stat.S_IWRITE
        return
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"


def test_v1_migration_no_deadlock(tmp_store: Path) -> None:
    """Concurrent reads of a v1 file must migrate safely without deadlock."""
    # Seed a v1 file
    v1_data = {
        "version": 1,
        "integrations": [
            {
                "id": "grafana-abc",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://example.com", "api_key": "k"},
            }
        ],
    }
    tmp_store.parent.mkdir(parents=True, exist_ok=True)
    tmp_store.write_text(json.dumps(v1_data) + "\n", encoding="utf-8")

    num_threads = 4
    results: list[dict[str, Any]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            data = _load_raw()
            with lock:
                results.append(data)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    _join_threads(threads)

    assert not errors, f"Worker exceptions: {errors}"

    # All threads should have observed v2 data
    for data in results:
        assert data.get("version") == 2
        records = data.get("integrations", [])
        assert len(records) == 1
        assert records[0].get("instances") is not None

    # On disk should be v2 as well
    final_data = _read_json(tmp_store)
    assert final_data.get("version") == 2


def test_lock_timeout_raises_store_specific_oserror(tmp_store: Path) -> None:
    """Lock acquisition timeouts should expose a specific OSError-compatible API."""

    class TimedOutLock:
        def __enter__(self) -> None:
            raise Timeout(str(tmp_store.with_suffix(".lock")))

        def __exit__(self, *_args: object) -> None:
            return None

    with (
        patch("app.integrations.store._acquire_lock", return_value=TimedOutLock()),
        pytest.raises(IntegrationStoreLockTimeout) as exc_info,
    ):
        upsert_integration("grafana", {"credentials": {"api_key": "k"}})

    assert isinstance(exc_info.value, OSError)
    assert str(tmp_store.with_suffix(".lock")) in str(exc_info.value)
