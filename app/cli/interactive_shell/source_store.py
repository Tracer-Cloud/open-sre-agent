"""SQLite-backed source chunk store for future interactive-shell RAG."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_STORE_PATH = Path.home() / ".config" / "opensre" / "source_index.sqlite"


class IncompatibleStoreError(RuntimeError):
    """Raised when an existing source store was built for different embeddings."""


@dataclass(frozen=True)
class StoredChunk:
    relpath: str
    kind: str
    symbol: str
    start_line: int
    end_line: int
    content: str
    fingerprint: str


class SourceStore:
    """Persist source chunks and float32 embedding vectors in SQLite."""

    def __init__(
        self,
        path: Path = DEFAULT_STORE_PATH,
        *,
        vector_dim: int | None = None,
        embedding_model: str | None = None,
    ) -> None:
        if vector_dim is not None and vector_dim <= 0:
            raise ValueError("vector_dim must be positive")

        self.path = path
        self._vector_dim: int | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()
        self._vector_dim = self._initialize_compatibility(
            vector_dim=vector_dim,
            embedding_model=embedding_model,
        )

    @property
    def embedding_model(self) -> str | None:
        return self.get_meta("embedding_model")

    def set_meta(self, key: str, value: str) -> None:
        if key == "vector_dim":
            value = self._normalize_vector_dim(value)
            if self._vector_dim is not None and int(value) != self._vector_dim:
                raise IncompatibleStoreError(f"Store vector_dim is {self._vector_dim}, got {value}")
            self._vector_dim = int(value)

        if key == "embedding_model":
            existing = self.get_meta(key)
            if existing is not None and existing != value:
                raise IncompatibleStoreError(
                    f"Store embedding_model is {existing!r}, got {value!r}"
                )

        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def file_fingerprints(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT relpath, fingerprint
                FROM chunks
                ORDER BY id
                """
            ).fetchall()
        return {str(row["relpath"]): str(row["fingerprint"]) for row in rows}

    def delete_file(self, relpath: str) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id FROM chunks WHERE relpath = ?", (relpath,)).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = _placeholders(ids)
                conn.execute(f"DELETE FROM vectors WHERE id IN ({placeholders})", ids)
            deleted = conn.execute("DELETE FROM chunks WHERE relpath = ?", (relpath,))
            conn.commit()
        return int(deleted.rowcount)

    def upsert_chunks(
        self,
        chunks: list[StoredChunk],
        vectors: list[np.ndarray],
    ) -> list[int]:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return []

        normalized_vectors = [self._normalize_vector(vector) for vector in vectors]
        relpaths = sorted({chunk.relpath for chunk in chunks})

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._delete_files_in_transaction(conn, relpaths)

            ids: list[int] = []
            for chunk, vector in zip(chunks, normalized_vectors, strict=True):
                cursor = conn.execute(
                    """
                    INSERT INTO chunks (
                        relpath,
                        kind,
                        symbol,
                        start_line,
                        end_line,
                        content,
                        fingerprint
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.relpath,
                        chunk.kind,
                        chunk.symbol,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content,
                        chunk.fingerprint,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return an inserted chunk id")
                chunk_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO vectors (id, vector) VALUES (?, ?)",
                    (chunk_id, vector.tobytes()),
                )
                ids.append(chunk_id)
            conn.commit()
        return ids

    def fetch_by_ids(self, ids: list[int]) -> list[StoredChunk]:
        if not ids:
            return []

        with self._connect() as conn:
            placeholders = _placeholders(ids)
            rows = conn.execute(
                f"""
                SELECT id, relpath, kind, symbol, start_line, end_line, content, fingerprint
                FROM chunks
                WHERE id IN ({placeholders})
                """,
                ids,
            ).fetchall()

        by_id = {int(row["id"]): _chunk_from_row(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]

    def cosine_topk(self, query_vector: np.ndarray, k: int = 30) -> list[tuple[int, float]]:
        if k <= 0:
            return []

        query = self._normalize_vector(query_vector)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return []

        with self._connect() as conn:
            rows = conn.execute("SELECT id, vector FROM vectors ORDER BY id").fetchall()
        if not rows:
            return []

        ids: list[int] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            ids.append(int(row["id"]))
            vectors.append(self._vector_from_blob(row["vector"]))

        matrix = np.vstack(vectors)
        norms = np.linalg.norm(matrix, axis=1)
        denominators = norms * query_norm
        scores = np.divide(
            matrix @ query,
            denominators,
            out=np.zeros_like(norms, dtype=np.float32),
            where=denominators != 0,
        )

        ordered = sorted(
            zip(ids, (float(score) for score in scores), strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return ordered[:k]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS chunk_count,
                    COUNT(DISTINCT relpath) AS file_count
                FROM chunks
                """
            ).fetchone()

        return {
            "chunk_count": int(row["chunk_count"]),
            "file_count": int(row["file_count"]),
            "on_disk_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "embedding_model": self.embedding_model,
            "vector_dim": self._vector_dim,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _create_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    relpath      TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    symbol       TEXT NOT NULL,
                    start_line   INTEGER NOT NULL,
                    end_line     INTEGER NOT NULL,
                    content      TEXT NOT NULL,
                    fingerprint  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_relpath ON chunks(relpath);
                CREATE TABLE IF NOT EXISTS vectors (
                    id INTEGER PRIMARY KEY,
                    vector BLOB NOT NULL,
                    FOREIGN KEY(id) REFERENCES chunks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _initialize_compatibility(
        self,
        *,
        vector_dim: int | None,
        embedding_model: str | None,
    ) -> int | None:
        existing_dim = self.get_meta("vector_dim")
        resolved_dim: int | None = None
        if existing_dim is not None:
            resolved_dim = int(self._normalize_vector_dim(existing_dim))
            if vector_dim is not None and vector_dim != resolved_dim:
                raise IncompatibleStoreError(
                    f"Store vector_dim is {resolved_dim}, got {vector_dim}"
                )
        elif vector_dim is not None:
            self.set_meta("vector_dim", str(vector_dim))
            resolved_dim = vector_dim

        if embedding_model is not None:
            existing_model = self.embedding_model
            if existing_model is not None and existing_model != embedding_model:
                raise IncompatibleStoreError(
                    f"Store embedding_model is {existing_model!r}, got {embedding_model!r}"
                )
            if existing_model is None:
                self.set_meta("embedding_model", embedding_model)

        return resolved_dim

    def _normalize_vector(self, vector: np.ndarray) -> np.ndarray:
        normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
        if self._vector_dim is None:
            self.set_meta("vector_dim", str(normalized.shape[0]))
        elif normalized.shape[0] != self._vector_dim:
            raise IncompatibleStoreError(
                f"Store vector_dim is {self._vector_dim}, got {normalized.shape[0]}"
            )
        return normalized

    def _vector_from_blob(self, blob: bytes) -> np.ndarray:
        vector = np.frombuffer(blob, dtype=np.float32)
        if self._vector_dim is not None and vector.shape[0] != self._vector_dim:
            raise IncompatibleStoreError(
                f"Stored vector has dim {vector.shape[0]}, expected {self._vector_dim}"
            )
        return vector

    def _delete_files_in_transaction(
        self, conn: sqlite3.Connection, relpaths: Iterable[str]
    ) -> None:
        relpath_list = list(relpaths)
        if not relpath_list:
            return

        placeholders = _placeholders(relpath_list)
        rows = conn.execute(
            f"SELECT id FROM chunks WHERE relpath IN ({placeholders})",
            relpath_list,
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            id_placeholders = _placeholders(ids)
            conn.execute(f"DELETE FROM vectors WHERE id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM chunks WHERE relpath IN ({placeholders})", relpath_list)

    @staticmethod
    def _normalize_vector_dim(value: str) -> str:
        try:
            vector_dim = int(value)
        except ValueError as exc:
            raise IncompatibleStoreError(f"Invalid vector_dim metadata: {value!r}") from exc
        if vector_dim <= 0:
            raise IncompatibleStoreError(f"Invalid vector_dim metadata: {value!r}")
        return str(vector_dim)


def _chunk_from_row(row: sqlite3.Row) -> StoredChunk:
    return StoredChunk(
        relpath=str(row["relpath"]),
        kind=str(row["kind"]),
        symbol=str(row["symbol"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        content=str(row["content"]),
        fingerprint=str(row["fingerprint"]),
    )


def _placeholders(values: Iterable[object]) -> str:
    return ", ".join("?" for _ in values)
