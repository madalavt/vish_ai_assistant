"""Notebooks, their source documents, and search over them."""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import CurrentUserId, SessionDep
from app.models.notebook import Document, Notebook
from app.rag.ingest import extracted_path, ingest_document, upload_path
from app.rag.parse import SUPPORTED_EXTENSIONS, ParseError, parser_for
from app.rag.store import vector_search
from app.schemas.notebook import (
    DocumentOut,
    NotebookCreate,
    NotebookDetailOut,
    NotebookOut,
    NotebookPatch,
    SearchHit,
    SearchRequest,
    SearchResponse,
    UrlDocumentCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])
documents_router = APIRouter(prefix="/api/documents", tags=["documents"])


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        notebook_id=document.notebook_id,
        title=document.title,
        source_type=document.source_type,
        status=document.status,
        error=document.error,
        chunk_count=document.chunk_count,
        source_meta=document.source_meta,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


async def _load_notebook(
    session: SessionDep, notebook_id: uuid.UUID, user_id: uuid.UUID
) -> Notebook:
    notebook = await session.scalar(
        select(Notebook).where(Notebook.id == notebook_id, Notebook.user_id == user_id)
    )
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")
    return notebook


# ---------------------------------------------------------------- notebooks


@router.get("", response_model=list[NotebookOut])
async def list_notebooks(session: SessionDep, user_id: CurrentUserId) -> list[NotebookOut]:
    """Notebooks with their document counts, so the list can show progress."""
    ready = func.count(Document.id).filter(Document.status == "ready")
    statement = (
        select(Notebook, func.count(Document.id), ready)
        .outerjoin(Document, Document.notebook_id == Notebook.id)
        .where(Notebook.user_id == user_id)
        .group_by(Notebook.id)
        .order_by(Notebook.updated_at.desc())
    )

    return [
        NotebookOut(
            id=notebook.id,
            title=notebook.title,
            description=notebook.description,
            created_at=notebook.created_at,
            updated_at=notebook.updated_at,
            document_count=total,
            ready_count=ready_count,
        )
        for notebook, total, ready_count in (await session.execute(statement)).all()
    ]


@router.post("", response_model=NotebookOut, status_code=status.HTTP_201_CREATED)
async def create_notebook(
    payload: NotebookCreate, session: SessionDep, user_id: CurrentUserId
) -> NotebookOut:
    notebook = Notebook(user_id=user_id, title=payload.title, description=payload.description)
    session.add(notebook)
    await session.commit()
    await session.refresh(notebook)
    return NotebookOut(
        id=notebook.id,
        title=notebook.title,
        description=notebook.description,
        created_at=notebook.created_at,
        updated_at=notebook.updated_at,
    )


@router.get("/{notebook_id}", response_model=NotebookDetailOut)
async def get_notebook(
    notebook_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> NotebookDetailOut:
    notebook = await session.scalar(
        select(Notebook)
        .where(Notebook.id == notebook_id, Notebook.user_id == user_id)
        .options(selectinload(Notebook.documents))
    )
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")

    documents = sorted(notebook.documents, key=lambda d: d.created_at)
    return NotebookDetailOut(
        id=notebook.id,
        title=notebook.title,
        description=notebook.description,
        created_at=notebook.created_at,
        updated_at=notebook.updated_at,
        document_count=len(documents),
        ready_count=sum(1 for d in documents if d.status == "ready"),
        documents=[_document_out(d) for d in documents],
    )


@router.patch("/{notebook_id}", response_model=NotebookOut)
async def patch_notebook(
    notebook_id: uuid.UUID,
    payload: NotebookPatch,
    session: SessionDep,
    user_id: CurrentUserId,
) -> NotebookOut:
    notebook = await _load_notebook(session, notebook_id, user_id)
    if payload.title is not None:
        notebook.title = payload.title
    if payload.description is not None:
        notebook.description = payload.description
    await session.commit()
    await session.refresh(notebook)
    return NotebookOut(
        id=notebook.id,
        title=notebook.title,
        description=notebook.description,
        created_at=notebook.created_at,
        updated_at=notebook.updated_at,
    )


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> None:
    notebook = await _load_notebook(session, notebook_id, user_id)

    # Remove files before the rows, so a failure cannot orphan them unreachably.
    for document in await session.scalars(
        select(Document).where(Document.notebook_id == notebook_id)
    ):
        _remove_files(document)

    await session.delete(notebook)
    await session.commit()


# ---------------------------------------------------------------- documents


@router.post(
    "/{notebook_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    notebook_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    user_id: CurrentUserId,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    """Accept a file and process it in the background.

    Returns 202: the row exists with status "pending" and the client polls it,
    rather than the request hanging for the length of a 50-page parse.
    """
    await _load_notebook(session, notebook_id, user_id)
    settings = get_settings()

    filename = file.filename or "untitled"
    try:
        parser_for(filename)
    except ParseError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    document = Document(
        notebook_id=notebook_id,
        user_id=user_id,
        title=filename,
        source_type=filename.rsplit(".", 1)[-1].lower(),
        status="pending",
    )
    session.add(document)
    await session.flush()

    destination = upload_path(document.id, filename)
    try:
        with destination.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        await file.close()

    size_mb = destination.stat().st_size / 1e6
    if size_mb > settings.max_upload_mb:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {size_mb:.1f} MB; the limit is {settings.max_upload_mb} MB",
        )

    document.source_uri = str(destination)
    await session.commit()
    await session.refresh(document)

    background.add_task(ingest_document, document.id)
    return _document_out(document)


@router.post(
    "/{notebook_id}/documents/url",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_url_document(
    notebook_id: uuid.UUID,
    payload: UrlDocumentCreate,
    background: BackgroundTasks,
    session: SessionDep,
    user_id: CurrentUserId,
) -> DocumentOut:
    await _load_notebook(session, notebook_id, user_id)

    document = Document(
        notebook_id=notebook_id,
        user_id=user_id,
        title=payload.title or str(payload.url),
        source_type="url",
        source_uri=str(payload.url),
        status="pending",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)

    background.add_task(ingest_document, document.id)
    return _document_out(document)


@router.get("/{notebook_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    notebook_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> list[DocumentOut]:
    """Polled by the UI while documents process."""
    await _load_notebook(session, notebook_id, user_id)
    documents = await session.scalars(
        select(Document).where(Document.notebook_id == notebook_id).order_by(Document.created_at)
    )
    return [_document_out(d) for d in documents]


# ------------------------------------------------------------------ search


@router.post("/{notebook_id}/search", response_model=SearchResponse)
async def search_notebook(
    notebook_id: uuid.UUID,
    payload: SearchRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> SearchResponse:
    """Vector search over a notebook's chunks.

    M3's proof that ingestion worked. M4 fuses this with keyword search.
    """
    await _load_notebook(session, notebook_id, user_id)

    results = await vector_search(
        session,
        query=payload.query,
        notebook_id=notebook_id,
        document_id=payload.document_id,
        limit=payload.limit,
    )
    if not results:
        return SearchResponse(query=payload.query, hits=[])

    titles = dict(
        (
            await session.execute(
                select(Document.id, Document.title).where(
                    Document.id.in_({chunk.document_id for chunk, _ in results})
                )
            )
        ).all()
    )

    return SearchResponse(
        query=payload.query,
        hits=[
            SearchHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=titles.get(chunk.document_id, "Unknown"),
                content=chunk.content,
                similarity=round(similarity, 4),
                page=chunk.page,
                end_page=chunk.end_page,
                section=chunk.section,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
            )
            for chunk, similarity in results
        ],
    )


# --------------------------------------------------------- single documents


def _remove_files(document: Document) -> None:
    """Delete a document's upload and extracted text, ignoring what is gone."""
    if document.source_type != "url" and document.source_uri:
        Path(document.source_uri).unlink(missing_ok=True)
    extracted_path(document.id).unlink(missing_ok=True)


async def _load_document(
    session: SessionDep, document_id: uuid.UUID, user_id: uuid.UUID
) -> Document:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@documents_router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> DocumentOut:
    return _document_out(await _load_document(session, document_id, user_id))


@documents_router.post(
    "/{document_id}/reprocess",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    session: SessionDep,
    user_id: CurrentUserId,
) -> DocumentOut:
    """Retry a failed document, or re-index after changing chunk settings."""
    document = await _load_document(session, document_id, user_id)
    document.status = "pending"
    document.error = None
    await session.commit()
    await session.refresh(document)

    background.add_task(ingest_document, document.id)
    return _document_out(document)


@documents_router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> None:
    document = await _load_document(session, document_id, user_id)
    _remove_files(document)
    await session.delete(document)
    await session.commit()


@documents_router.get("/supported/extensions")
async def supported_extensions() -> dict[str, list[str]]:
    return {"extensions": list(SUPPORTED_EXTENSIONS)}
