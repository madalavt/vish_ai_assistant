"""Embedding and vector storage.

This module owns the document/query prefix distinction. nomic-embed-text was
trained with `search_document: ` on stored passages and `search_query: ` on
queries; mixing them up produces no error at all, just quietly worse retrieval.
Keeping both sides here means a caller cannot get it wrong.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import get_registry
from app.models.notebook import Chunk as ChunkRow
from app.models.notebook import Document
from app.rag.chunk import Chunk

logger = logging.getLogger(__name__)

# Ollama handles a list per call; batching keeps requests bounded without
# paying per-request overhead on every chunk.
EMBED_BATCH_SIZE = 32


async def embed_and_store(
    session: AsyncSession,
    *,
    document: Document,
    chunks: list[Chunk],
) -> int:
    """Embed chunks and replace the document's existing ones.

    Replacing rather than appending makes re-ingestion idempotent: processing
    the same document twice leaves one set of chunks, not two.
    """
    settings = get_settings()
    registry = get_registry()

    await session.execute(delete(ChunkRow).where(ChunkRow.document_id == document.id))

    if not chunks:
        return 0

    stored = 0
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = await registry.embed_documents([c.text for c in batch])

        for chunk, vector in zip(batch, vectors, strict=True):
            if len(vector) != settings.embedding_dim:
                raise ValueError(
                    f"Embedding dimension {len(vector)} does not match the "
                    f"vector({settings.embedding_dim}) column. Changing embedding "
                    "model requires a migration and re-ingesting every document."
                )
            session.add(
                ChunkRow(
                    document_id=document.id,
                    notebook_id=document.notebook_id,
                    content=chunk.text,
                    embedding=vector,
                    position=chunk.position,
                    page=chunk.page,
                    end_page=chunk.end_page,
                    section=chunk.section,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                )
            )
            stored += 1

        logger.info("embedded %s/%s chunks for document %s", stored, len(chunks), document.id)

    return stored


async def vector_search(
    session: AsyncSession,
    *,
    query: str,
    notebook_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = 10,
) -> list[tuple[ChunkRow, float]]:
    """Cosine similarity search. Returns (chunk, similarity) with 1.0 best.

    pgvector's `<=>` is cosine *distance*, so it is converted to similarity
    here — a raw distance ordering reads backwards everywhere else.

    M4 adds keyword search fused with this via RRF; this is the vector half.
    """
    registry = get_registry()
    embedding = await registry.embed_query(query)

    distance = ChunkRow.embedding.cosine_distance(embedding).label("distance")
    statement = select(ChunkRow, distance)

    if notebook_id is not None:
        statement = statement.where(ChunkRow.notebook_id == notebook_id)
    if document_id is not None:
        statement = statement.where(ChunkRow.document_id == document_id)

    statement = statement.order_by(distance).limit(limit)

    rows = (await session.execute(statement)).all()
    return [(row[0], 1.0 - float(row[1])) for row in rows]
