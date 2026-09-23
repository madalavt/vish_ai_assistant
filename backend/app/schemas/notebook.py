"""Notebook and document API shapes."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class NotebookOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    ready_count: int = 0


class NotebookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class NotebookPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    notebook_id: uuid.UUID
    title: str
    source_type: str
    status: str
    error: str | None
    chunk_count: int
    source_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class NotebookDetailOut(NotebookOut):
    documents: list[DocumentOut]


class UrlDocumentCreate(BaseModel):
    url: HttpUrl
    title: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    document_id: uuid.UUID | None = None


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    similarity: float
    page: int | None
    end_page: int | None
    section: str | None
    start_char: int | None
    end_char: int | None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
