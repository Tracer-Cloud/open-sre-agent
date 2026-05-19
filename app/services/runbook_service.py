"""RunbookService: per-tenant runbook ingestion and vector search."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Sequence

import tiktoken
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_current_tenant, set_current_tenant
from app.db.repository import TenantRepository
from app.state.runbook import EMBEDDING_DIM, Runbook

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = os.environ.get("RUNBOOK_EMBEDDING_MODEL", "text-embedding-3-small")
_MAX_TOKENS = 8000  # chunk threshold before embedding
_CHUNK_OVERLAP = 200  # token overlap between adjacent chunks


def _get_tokenizer() -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(_EMBEDDING_MODEL)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _chunk_text(text: str, max_tokens: int = _MAX_TOKENS) -> list[str]:
    """Split text into chunks that fit within max_tokens, with overlap."""
    enc = _get_tokenizer()
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += max_tokens - _CHUNK_OVERLAP
    return chunks


def _embed(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via OpenAI text-embedding-3-small (dim=1536)."""
    client = OpenAI()
    response = client.embeddings.create(
        input=texts,
        model=_EMBEDDING_MODEL,
        dimensions=EMBEDDING_DIM,
    )
    return [item.embedding for item in response.data]


def _average_embeddings(embeddings: list[list[float]]) -> list[float]:
    """Average multiple chunk embeddings into one representative vector."""
    if len(embeddings) == 1:
        return embeddings[0]
    n = len(embeddings[0])
    avg = [0.0] * n
    for emb in embeddings:
        for i, v in enumerate(emb):
            avg[i] += v
    count = len(embeddings)
    return [v / count for v in avg]


class RunbookRepository(TenantRepository[Runbook]):
    """Runbook-specific queries on top of TenantRepository."""

    def __init__(self, db: Session) -> None:
        super().__init__(db, Runbook)

    def get_by_title(self, title: str) -> Runbook | None:
        return self.query().filter(Runbook.title == title).first()  # type: ignore[return-value]

    def search_by_embedding(
        self, embedding: list[float], top_k: int
    ) -> list[Runbook]:
        results = (
            self.db.execute(
                select(Runbook)
                .where(Runbook.tenant_id == get_current_tenant())
                .order_by(Runbook.embedding.cosine_distance(embedding))
                .limit(top_k)
            )
            .scalars()
            .all()
        )
        return list(results)


class RunbookService:
    """Manages runbook storage and semantic retrieval for a single DB session."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = RunbookRepository(db)

    def upsert(
        self,
        *,
        tenant_id: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        source_url: str | None = None,
    ) -> Runbook:
        """Store or update a runbook; (re)generates its embedding."""
        chunks = _chunk_text(content)
        embeddings = _embed(chunks)
        embedding = _average_embeddings(embeddings)

        set_current_tenant(tenant_id)
        existing = self._repo.get_by_title(title)

        if existing:
            existing.content = content
            existing.embedding = embedding
            existing.tags = tags or []
            existing.source_url = source_url
            self._db.flush()
            return existing

        runbook = Runbook(
            id=str(uuid.uuid4()),
            title=title,
            content=content,
            embedding=embedding,
            tags=tags or [],
            source_url=source_url,
        )
        self._repo.add(runbook)
        self._db.flush()
        return runbook

    def search(
        self, *, tenant_id: str, query: str, top_k: int = 3
    ) -> Sequence[Runbook]:
        """Return top_k runbooks most similar to query, scoped to tenant."""
        set_current_tenant(tenant_id)
        query_emb = _embed([query])[0]
        return self._repo.search_by_embedding(query_emb, top_k)

    def delete(self, *, tenant_id: str, runbook_id: uuid.UUID) -> bool:
        """Delete a runbook; returns True if it existed."""
        set_current_tenant(tenant_id)
        runbook = self._repo.get(str(runbook_id))
        if runbook is None:
            return False
        self._db.delete(runbook)
        self._db.flush()
        return True
