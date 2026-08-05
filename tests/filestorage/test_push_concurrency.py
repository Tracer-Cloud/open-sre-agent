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
from unittest import mock

import pytest

from platform.filestorage.engine import (
    _PUSH_TRANSFER_WORKERS,
    SyncDirection,
    SyncProgress,
    push,
)
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import RemoteSyncUnavailableError, UnsyncablePathError
from platform.filestorage.ports import RemoteObject
from platform.filestorage.syncable import SyncRoot

# More files than the worker cap, so bounded concurrency is observable: with
# the cap at N, in-flight uploads plateau at N while the rest wait their turn.
_FILE_COUNT = _PUSH_TRANSFER_WORKERS * 2

# Far more files than the worker cap, so the pool holds a long backlog of
# not-yet-started work: a fail-fast push must cancel that backlog rather than
# drain it, leaving strictly fewer than every file uploaded.
_FAIL_FAST_FILE_COUNT = _PUSH_TRANSFER_WORKERS * 10

# How long a queued upload holds while the fail-fast path surfaces the first
# error and cancels the backlog. Long enough for the cancel to win the race,
# short enough that teardown of any in-flight peers stays quick.
_PEER_HOLD_SECONDS = 0.5

# Files for the ordering test. Kept at or below the worker cap so every upload
# is genuinely in flight at once — the reverse-completion barrier below can only
# drive all N to finish in reverse order when none are still queued.
_ORDER_FILE_COUNT = 6

# Barrier/event ceiling for the ordering test: a stuck upload fails fast rather
# than hanging the suite.
_BARRIER_TIMEOUT_SECONDS = 5.0


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


class _FailFastStore:
    """The first upload raises while a large backlog is still queued.

    A fail-fast push surfaces the first failure and cancels the queued backlog,
    so only the handful of uploads the pool has already picked up are ever
    attempted — far fewer than the whole plan. A push that drained the executor
    on failure would instead attempt every file. This pins the backlog-cancel
    property so a future rewrite (e.g. ``wait(ALL_COMPLETED)`` then aggregate)
    cannot silently regress it.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self.attempts = 0
        self._blocker = threading.Event()

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [obj for obj in _listing(self.objects) if obj.key.startswith(prefix)]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        with self._lock:
            self.attempts += 1
            first = self.attempts == 1
        if first:
            raise RemoteSyncUnavailableError("bucket unreachable")
        # Hold peers briefly so the failure surfaces and the engine cancels the
        # queued backlog before a freed worker can drain it. A short wait is
        # enough for the cancel to land and keeps teardown fast.
        self._blocker.wait(timeout=_PEER_HOLD_SECONDS)
        with self._lock:
            self.objects[key] = data

    def describe(self) -> str:
        return "failfast://store"


def test_upload_failure_stops_remaining_transfers(tmp_path: Path) -> None:
    """A failed upload cancels the backlog instead of draining the whole plan."""
    # Arrange: far more files than the worker cap, so most sit queued when the
    # first upload fails.
    roots = _make_root(tmp_path, _FAIL_FAST_FILE_COUNT)
    store = _FailFastStore()

    # Act / Assert: the transient error propagates, exactly as the sequential
    # push surfaced it on the first failing upload.
    with pytest.raises(RemoteSyncUnavailableError):
        push(store, roots=roots)

    # Fail-fast: once the failure surfaces the queued backlog is cancelled, so
    # only work already picked up by the pool is ever attempted — a small
    # multiple of the worker cap, never the whole plan. A draining push would
    # attempt every file. The margin above the cap absorbs the brief race
    # between the failed worker freeing up and the cancel landing.
    assert store.attempts <= _PUSH_TRANSFER_WORKERS * 2
    assert store.attempts < _FAIL_FAST_FILE_COUNT


def test_progress_reports_land_between_uploads_not_only_at_the_end(
    tmp_path: Path,
) -> None:
    """Some uploads run after progress has already fired for earlier ones.

    Each ``put_object`` records how many progress events have fired so far. A
    push that reports incrementally lets an upload observe a non-zero count
    (an earlier file already reported); a push that defers every report until
    all uploads return leaves that count at zero for every single upload.
    """
    # Arrange: serialise the uploads (cap 1) so ordering is deterministic and
    # the only way a later upload sees prior progress is incremental reporting.
    roots = _make_root(tmp_path, _FILE_COUNT)
    progress_count = 0
    counts_seen_at_upload: list[int] = []
    lock = threading.Lock()

    class _ObservingStore:
        objects: dict[str, bytes] = {}

        def list_objects(self, prefix: str) -> list[RemoteObject]:
            return [obj for obj in _listing(self.objects) if obj.key.startswith(prefix)]

        def get_object(self, key: str) -> bytes:
            return self.objects[key]

        def put_object(self, key: str, data: bytes) -> None:
            with lock:
                counts_seen_at_upload.append(progress_count)
            self.objects[key] = data

        def describe(self) -> str:
            return "observing://store"

    def _on_progress(_progress: SyncProgress) -> None:
        nonlocal progress_count
        with lock:
            progress_count += 1

    # Act: a single worker makes upload/report interleaving deterministic.
    with mock.patch("platform.filestorage.engine._PUSH_TRANSFER_WORKERS", 1):
        push(_ObservingStore(), roots=roots, on_progress=_on_progress)

    # Assert: at least one upload ran after an earlier file already reported.
    # Deferred reporting would leave every entry at zero.
    assert max(counts_seen_at_upload) > 0


def test_progress_reports_once_per_planned_file(tmp_path: Path) -> None:
    """The push emits exactly one progress event per file, counting to total."""
    # Arrange
    roots = _make_root(tmp_path, _FILE_COUNT)
    store = _CountingStore(expected_peak=_PUSH_TRANSFER_WORKERS)
    seen: list[SyncProgress] = []

    # Act
    push(store, roots=roots, on_progress=seen.append)

    # Assert: one report per planned file, counting up 1..total in the push
    # direction.
    assert len(seen) == _FILE_COUNT
    assert sorted(p.completed for p in seen) == list(range(1, _FILE_COUNT + 1))
    assert all(p.direction is SyncDirection.PUSH for p in seen)
    assert all(p.total == _FILE_COUNT for p in seen)


def _index_of(key: str) -> int:
    """Planned index encoded in a ``sessions/s{i}.jsonl`` key."""
    return int(key.removeprefix("sessions/s").removesuffix(".jsonl"))


class _ReverseCompletionStore:
    """Forces uploads to COMPLETE in the exact reverse of planned order.

    Every upload first gathers at a barrier, so all N are genuinely in flight at
    once; then file index ``i`` is held until file ``i + 1`` has finished. The
    last planned file therefore completes first and the first planned file
    completes last. A report that folded outcomes in completion order would come
    out reversed — this store exists to make that reversal happen on purpose so
    the planned-order fold is pinned against it.
    """

    def __init__(self, count: int) -> None:
        self.objects: dict[str, bytes] = {}
        self.completion_order: list[str] = []
        self._count = count
        # All N uploads must arrive before any may finish, so they overlap and
        # the reverse-release chain below has every peer waiting.
        self._gate = threading.Barrier(count, timeout=_BARRIER_TIMEOUT_SECONDS)
        self._done = [threading.Event() for _ in range(count)]
        self._lock = threading.Lock()

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [obj for obj in _listing(self.objects) if obj.key.startswith(prefix)]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        index = _index_of(key)
        self._gate.wait()
        # Release strictly last-to-first: hold until the next planned file is done.
        if index + 1 < self._count:
            self._done[index + 1].wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        self.objects[key] = data
        with self._lock:
            self.completion_order.append(key)
        self._done[index].set()

    def describe(self) -> str:
        return "reverse://store"


def test_report_folds_in_planned_order_not_completion_order(tmp_path: Path) -> None:
    """uploaded keys follow planned order even when uploads finish reversed.

    ``as_completed`` yields futures in whatever order they finish, so folding the
    report as they arrive lets a slow-first/fast-last transfer produce a
    non-deterministic ``uploaded`` ordering. The engine folds in planned order
    instead; this pins that the report is stable against the reversed completion
    the store deliberately produces.
    """
    # Arrange
    roots = _make_root(tmp_path, _ORDER_FILE_COUNT)
    store = _ReverseCompletionStore(_ORDER_FILE_COUNT)

    # Act
    report = push(store, roots=roots)

    # Assert: the store really did finish in reverse (else the test proves
    # nothing), yet the report is in planned order — not that completion order.
    planned_order = [f"sessions/s{i}.jsonl" for i in range(_ORDER_FILE_COUNT)]
    assert store.completion_order == list(reversed(planned_order))
    assert report.uploaded == planned_order
