"""Ingestion: parse, chunk, embed, store — with status the UI can follow.

Runs in the background after the upload responds, so a large PDF does not hold
the request open. Every failure is written to the document row rather than
logged and forgotten, because a document stuck at "processing" with no reason
is the worst outcome for someone waiting on it.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.models.notebook import Document
from app.rag.chunk import chunk_document
from app.rag.parse import ParseError, parse_file, parse_url
from app.rag.store import embed_and_store

logger = logging.getLogger(__name__)


def upload_path(document_id: uuid.UUID, filename: str) -> Path:
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings.upload_dir / f"{document_id}{Path(filename).suffix.lower()}"


def extracted_path(document_id: uuid.UUID) -> Path:
    settings = get_settings()
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    return settings.extracted_dir / f"{document_id}.md"


async def ingest_document(document_id: uuid.UUID) -> None:
    """Process one document end to end.

    Opens its own session: this runs after the upload response has been sent,
    by which point a request-scoped session is closed.
    """
    settings = get_settings()

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            logger.warning("ingest: document %s vanished before processing", document_id)
            return

        document.status = "processing"
        document.error = None
        await session.commit()

        source_type = document.source_type
        source_uri = document.source_uri
        title = document.title

    error: str | None = None
    try:
        if source_type == "url":
            if not source_uri:
                raise ParseError("No URL recorded for this document")
            # The row is created with the URL as a placeholder title so the UI
            # has something to show while it is pending. Passing that through
            # would suppress the real page title, so drop it here.
            supplied = title if title != source_uri else None
            parsed = parse_url(source_uri, title=supplied)
        else:
            path = Path(source_uri) if source_uri else None
            if path is None or not path.exists():
                raise ParseError("Uploaded file is missing from disk")
            parsed = parse_file(path, filename=path.name, title=title)

        chunks, full_text = chunk_document(
            parsed,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )
        if not chunks:
            raise ParseError("Nothing to index — the document produced no text")

        # Kept so M4's source panel can show surrounding context, which the
        # chunk text alone cannot provide.
        extracted_path(document_id).write_text(full_text, encoding="utf-8")

        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return

            stored = await embed_and_store(session, document=document, chunks=chunks)

            document.status = "ready"
            document.chunk_count = stored
            document.error = None
            document.title = parsed.title or document.title
            document.source_meta = {
                **parsed.meta,
                "blocks": len(parsed.blocks),
                "characters": len(full_text),
            }
            await session.commit()

        logger.info("ingest: %s ready with %s chunks", document_id, stored)
        return

    except ParseError as exc:
        error = str(exc)
    except Exception as exc:
        logger.exception("ingest failed for %s", document_id)
        error = f"{type(exc).__name__}: {exc}"

    async with SessionLocal() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="error", error=error, chunk_count=0)
        )
        await session.commit()


async def fail_interrupted_documents() -> int:
    """Clear documents left mid-processing by a restart.

    Without this they sit at "processing" forever with no explanation, and no
    way for the user to tell a stuck document from a slow one.
    """
    async with SessionLocal() as session:
        stuck = list(
            await session.scalars(select(Document.id).where(Document.status == "processing"))
        )
        if not stuck:
            return 0

        await session.execute(
            update(Document)
            .where(Document.id.in_(stuck))
            .values(
                status="error",
                error="Processing was interrupted by a server restart. Re-process to retry.",
            )
        )
        await session.commit()

    logger.warning("ingest: marked %s interrupted document(s) as failed", len(stuck))
    return len(stuck)
