"""Notebooks, their source documents, and the embedded chunks behind retrieval."""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin

EMBEDDING_DIM = get_settings().embedding_dim

DOCUMENT_STATUSES = ("pending", "processing", "ready", "error")


class Notebook(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notebooks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="notebook", cascade="all, delete-orphan"
    )


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    notebook_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # "pdf", "docx", "md", "txt", "url"
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Parser output that resists a fixed schema: page map, tables, heading tree.
    source_meta: Mapped[dict[str, Any]] = mapped_column(
        "source_meta", JSONB, nullable=False, default=dict
    )

    notebook: Mapped[Notebook] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_documents_notebook_status", "notebook_id", "status"),)


class Chunk(UUIDMixin, TimestampMixin, Base):
    """One embedded passage.

    Carries both halves of hybrid retrieval: the vector for semantic similarity
    and a generated tsvector for keyword ranking, fused with RRF in M4. Page and
    character offsets are what let a citation resolve back to the exact passage.
    """

    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    # Citation anchors.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # A chunk may span pages when pages are short. Storing only the start page
    # would make a citation for text on the last page point at the first.
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)

    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=False,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        # HNSW for cosine similarity. Built with raised maintenance_work_mem
        # (set in docker-compose.yml) or the build is painfully slow.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        Index("ix_chunks_notebook", "notebook_id"),
        Index("ix_chunks_document_position", "document_id", "position", unique=True),
    )


# Keep a reference so linters do not flag the import used only in Computed().
_ = text
