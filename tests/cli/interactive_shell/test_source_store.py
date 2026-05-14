"""Tests for the SQLite-backed interactive-shell source store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from app.cli.interactive_shell.source_store import (
    IncompatibleStoreError,
    SourceStore,
    StoredChunk,
)


def _chunk(
    relpath: str,
    symbol: str,
    *,
    fingerprint: str = "fp",
    content: str | None = None,
) -> StoredChunk:
    return StoredChunk(
        relpath=relpath,
        kind="py_func",
        symbol=symbol,
        start_line=1,
        end_line=3,
        content=content or f"def {symbol}(): ...",
        fingerprint=fingerprint,
    )


def _store(tmp_path: Path, *, vector_dim: int | None = 3) -> SourceStore:
    return SourceStore(tmp_path / "source.sqlite", vector_dim=vector_dim)


def test_round_trip_upsert_and_fetch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    chunks = [_chunk("app/a.py", "alpha"), _chunk("app/b.py", "beta")]
    vectors = [
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        np.array([0.0, 1.0, 0.0], dtype=np.float32),
    ]

    ids = store.upsert_chunks(chunks, vectors)

    assert store.fetch_by_ids(ids) == chunks


def test_upsert_replaces_existing_chunks_for_same_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_ids = store.upsert_chunks(
        [_chunk("app/a.py", "alpha", fingerprint="old")],
        [np.array([1.0, 0.0, 0.0])],
    )
    second = _chunk("app/a.py", "replacement", fingerprint="new")

    second_ids = store.upsert_chunks([second], [np.array([0.0, 1.0, 0.0])])

    assert store.fetch_by_ids(first_ids) == []
    assert store.fetch_by_ids(second_ids) == [second]
    assert store.file_fingerprints() == {"app/a.py": "new"}


def test_delete_file_removes_chunks_and_vectors(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = store.upsert_chunks(
        [_chunk("app/a.py", "alpha"), _chunk("app/b.py", "beta")],
        [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])],
    )

    deleted = store.delete_file("app/a.py")

    assert deleted == 1
    assert store.fetch_by_ids([ids[0]]) == []
    assert store.fetch_by_ids([ids[1]]) == [_chunk("app/b.py", "beta")]
    assert store.cosine_topk(np.array([1.0, 0.0, 0.0]))[0][0] == ids[1]


def test_fetch_by_ids_batches_large_id_lists(tmp_path: Path) -> None:
    store = SourceStore(tmp_path / "source.sqlite", vector_dim=1)
    chunks = [_chunk(f"app/{index}.py", f"chunk_{index}") for index in range(1005)]
    vectors = [np.array([float(index + 1)]) for index in range(1005)]
    ids = store.upsert_chunks(chunks, vectors)

    fetched = store.fetch_by_ids(ids)

    assert fetched == chunks


def test_cosine_topk_returns_expected_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = store.upsert_chunks(
        [
            _chunk("app/a.py", "alpha"),
            _chunk("app/b.py", "beta"),
            _chunk("app/c.py", "gamma"),
        ],
        [
            np.array([1.0, 0.0, 0.0]),
            np.array([0.5, 0.5, 0.0]),
            np.array([0.0, 1.0, 0.0]),
        ],
    )

    results = store.cosine_topk(np.array([1.0, 0.0, 0.0]), k=2)

    assert [chunk_id for chunk_id, _score in results] == [ids[0], ids[1]]
    assert results[0][1] > results[1][1]


def test_cosine_topk_empty_store_does_not_persist_vector_dim(tmp_path: Path) -> None:
    store = SourceStore(tmp_path / "source.sqlite")

    assert store.cosine_topk(np.array([1.0, 0.0, 0.0])) == []

    assert store.get_meta("vector_dim") is None


def test_failed_first_upsert_does_not_persist_vector_dim(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite"
    store = SourceStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_chunk_insert
            BEFORE INSERT ON chunks
            BEGIN
                SELECT RAISE(ABORT, 'forced chunk insert failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced chunk insert failure"):
        store.upsert_chunks(
            [_chunk("app/a.py", "alpha")],
            [np.array([1.0, 0.0, 0.0])],
        )

    assert store.get_meta("vector_dim") is None
    assert store.stats()["vector_dim"] is None


def test_cosine_topk_scan_limit_blocks_large_in_memory_load(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [_chunk("app/a.py", "alpha"), _chunk("app/b.py", "beta")],
        [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])],
    )

    with pytest.raises(RuntimeError, match="Refusing to scan 2 vectors"):
        store.cosine_topk(np.array([1.0, 0.0, 0.0]), max_scan_rows=1)


def test_cosine_topk_counts_and_reads_vectors_in_one_transaction(tmp_path: Path) -> None:
    traced_sql: list[str] = []

    class TracedSourceStore(SourceStore):
        def _connect(self) -> sqlite3.Connection:
            conn = super()._connect()
            conn.set_trace_callback(traced_sql.append)
            return conn

    store = TracedSourceStore(tmp_path / "source.sqlite", vector_dim=3)
    store.upsert_chunks(
        [_chunk("app/a.py", "alpha")],
        [np.array([1.0, 0.0, 0.0])],
    )
    traced_sql.clear()

    store.cosine_topk(np.array([1.0, 0.0, 0.0]))

    normalized_sql = [statement.strip().upper() for statement in traced_sql]
    begin_index = normalized_sql.index("BEGIN")
    count_index = normalized_sql.index("SELECT COUNT(*) AS ROW_COUNT FROM VECTORS")
    select_index = normalized_sql.index("SELECT ID, VECTOR FROM VECTORS ORDER BY ID")
    commit_index = normalized_sql.index("COMMIT")
    assert begin_index < count_index < select_index < commit_index


def test_incompatible_store_error_on_mismatched_vector_dim(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite"
    SourceStore(path, vector_dim=3)

    with pytest.raises(IncompatibleStoreError, match="vector_dim"):
        SourceStore(path, vector_dim=2)


def test_upsert_rejects_mixed_vector_dims_in_one_batch(tmp_path: Path) -> None:
    store = SourceStore(tmp_path / "source.sqlite")

    with pytest.raises(ValueError, match="one upsert batch"):
        store.upsert_chunks(
            [_chunk("app/a.py", "alpha"), _chunk("app/b.py", "beta")],
            [np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0])],
        )


def test_incompatible_store_error_on_mismatched_embedding_model(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite"
    SourceStore(path, vector_dim=3, embedding_model="model-a")

    with pytest.raises(IncompatibleStoreError, match="embedding_model"):
        SourceStore(path, vector_dim=3, embedding_model="model-b")


def test_stats_reports_counts_after_upsert_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_meta("embedding_model", "unit-test")
    store.upsert_chunks(
        [
            _chunk("app/a.py", "alpha"),
            _chunk("app/a.py", "beta"),
            _chunk("app/b.py", "gamma"),
        ],
        [
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        ],
    )

    before = store.stats()
    store.delete_file("app/a.py")
    after = store.stats()

    assert before["chunk_count"] == 3
    assert before["file_count"] == 2
    assert before["embedding_model"] == "unit-test"
    assert before["on_disk_bytes"] > 0
    assert after["chunk_count"] == 1
    assert after["file_count"] == 1


def test_file_fingerprints_returns_deduplicated_mapping(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk("app/a.py", "alpha", fingerprint="fp-a"),
            _chunk("app/a.py", "beta", fingerprint="fp-a"),
            _chunk("app/b.py", "gamma", fingerprint="fp-b"),
        ],
        [
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        ],
    )

    assert store.file_fingerprints() == {"app/a.py": "fp-a", "app/b.py": "fp-b"}


def test_file_fingerprints_uses_latest_chunk_when_fingerprints_disagree(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.sqlite"
    store = SourceStore(path, vector_dim=3)
    ids = store.upsert_chunks(
        [
            _chunk("app/a.py", "alpha", fingerprint="fp-current"),
            _chunk("app/a.py", "beta", fingerprint="fp-current"),
        ],
        [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])],
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE chunks SET fingerprint = ? WHERE id = ?",
            ("zz-stale-fingerprint", ids[0]),
        )

    assert store.file_fingerprints() == {"app/a.py": "fp-current"}


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite"
    first = SourceStore(path, vector_dim=3, embedding_model="unit-test")
    first.upsert_chunks([_chunk("app/a.py", "alpha")], [np.array([1.0, 0.0, 0.0])])

    second = SourceStore(path, vector_dim=3, embedding_model="unit-test")

    assert second.fetch_by_ids([1]) == [_chunk("app/a.py", "alpha")]
