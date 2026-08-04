"""Push transfers run with bounded concurrency after validation completes.

The engine validates every candidate (phase 1) before a single upload starts
(phase 2). Concurrency lives entirely in phase 2, so these tests pin three
things: the report is identical to a sequential run, no upload starts until
validation has cleared, and the uploads actually overlap under a bounded cap.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from platform.filestorage.engine import _PUSH_TRANSFER_WORKERS, push
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import UnsyncablePathError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.syncable import SyncRoot

# More files than the worker cap, so bounded concurrency is observable: with
# the cap at N, in-flight uploads plateau at N while the rest wait their turn.
_FILE_COUNT = _PUSH_TRANSFER_WORKERS * 2


def _listing(objects: dict[str, bytes]) -> list[RemoteObject]:
    """Render a stored-objects map as a bucket listing the engine can compare."""
    return [
        RemoteObject(key=key, size=len(data), last_modified=datetime.now(tz=UTC))
        for key, data in objects.items()
    ]


class _CountingStore:
    """Records peak concurrent ``put_object`` calls and every stored key.

    Each upload blocks on a barrier until enough peers have arrived, so a truly
    sequential engine (peak 1) would deadlock the barrier — instead the barrier
    carries a timeout and the test asserts the peak that was reached.
    """

    def __init__(self, *, expected_peak: int) -> None:
        self.objects: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak_in_flight = 0
        # Release once ``expected_peak`` uploads are simultaneously in flight, so
        # a real overlap is proven rather than inferred from timing.
        self._gate = threading.Barrier(expected_peak, timeout=5.0)

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [obj for obj in _listing(self.objects) if obj.key.startswith(prefix)]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        with self._lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            self._gate.wait()
        finally:
            with self._lock:
                self._in_flight -= 1
        # Distinct keys per thread: a plain dict item-set is atomic under the GIL.
        self.objects[key] = data

    def describe(self) -> str:
        return "counting://store"


class _OrderRecordingStore:
    """Fails validation for one root while recording whether any upload ran."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [obj for obj in _listing(self.objects) if obj.key.startswith(prefix)]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def describe(self) -> str:
        return "order://store"


def _make_root(base: Path, count: int) -> tuple[SyncRoot, ...]:
    sessions = base / "sessions"
    sessions.mkdir()
    for index in range(count):
        (sessions / f"s{index}.jsonl").write_text(f'{{"turn": {index}}}\n', encoding="utf-8")
    return (SyncRoot(name=SyncRootName.SESSIONS, path=sessions),)


def test_all_files_upload_and_report_matches_sequential(tmp_path: Path) -> None:
    """Every planned file is transferred and the report totals are exact."""
    # Arrange
    roots = _make_root(tmp_path, _FILE_COUNT)
    store = _CountingStore(expected_peak=_PUSH_TRANSFER_WORKERS)

    # Act
    report = push(store, roots=roots)

    # Assert: same keys and byte total a one-at-a-time engine would produce.
    expected_keys = {f"sessions/s{i}.jsonl" for i in range(_FILE_COUNT)}
    assert set(store.objects) == expected_keys
    assert set(report.uploaded) == expected_keys
    assert report.uploaded_bytes == sum(len(b) for b in store.objects.values())
    assert report.skipped == 0
    assert report.kept_remote == []


def test_uploads_overlap_within_the_bounded_cap(tmp_path: Path) -> None:
    """Concurrency is real (peak > 1) yet never exceeds the worker cap."""
    # Arrange
    roots = _make_root(tmp_path, _FILE_COUNT)
    store = _CountingStore(expected_peak=_PUSH_TRANSFER_WORKERS)

    # Act
    push(store, roots=roots)

    # Assert: overlap happened, and the cap held.
    assert store.peak_in_flight > 1
    assert store.peak_in_flight <= _PUSH_TRANSFER_WORKERS


def test_validation_failure_prevents_every_upload(tmp_path: Path) -> None:
    """A denied file anywhere refuses the whole push before any upload starts."""
    # Arrange: first root is clean, second reaches a denied credential file.
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "ok.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (tmp_path / "integrations.json").write_text('{"datadog": {"api_key": "x"}}', encoding="utf-8")
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=tmp_path / "sessions"),
        SyncRoot(name="everything", path=tmp_path),
    )
    store = _OrderRecordingStore()

    # Act / Assert
    with pytest.raises(UnsyncablePathError):
        push(store, roots=roots)
    assert store.objects == {}
