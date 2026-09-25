"""Grounded chat over a notebook's sources.

Reuses the conversations and messages tables — a notebook chat is just a
conversation with `notebook_id` set — so persistence, listing and deletion all
come for free rather than being rebuilt.

Wire format extends the chat tab's with one event: `sources`, sent before any
token so the UI can render citation markers as they stream in.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db import SessionLocal
from app.deps import CurrentUserId, SessionDep
from app.llm import ChatMessage, ProviderError, get_registry
from app.models.chat import Conversation, Message, title_from
from app.models.notebook import Notebook
from app.rag.answer import (
    NO_SOURCES_MESSAGE,
    build_messages,
    cited_indices,
    strip_invalid_citations,
)
from app.rag.retrieve import Retrieved, hybrid_search
from app.schemas.chat import DoneEvent, ErrorEvent, TokenEvent
from app.schemas.notebook import (
    NotebookChatRequest,
    NotebookChatStart,
    SourceOut,
    SourcesEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notebooks", tags=["notebook-chat"])

CANCELLED = "cancelled"

# How many passages to put in front of the model. Enough to cover a question
# from several angles, few enough that an 8B model still attends to all of them.
RETRIEVAL_LIMIT = 8

# Prior turns kept for follow-ups like "why?". Their sources are not re-included;
# only the current turn's passages are citable.
HISTORY_TURNS = 6


def _sse(event: str, payload: BaseModel | dict) -> str:
    data = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


def _source_out(index: int, hit: Retrieved) -> SourceOut:
    return SourceOut(
        number=index,
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        document_title=hit.document_title,
        content=hit.content,
        page=hit.page,
        end_page=hit.end_page,
        section=hit.section,
        start_char=hit.start_char,
        end_char=hit.end_char,
        score=round(hit.score, 6),
        vector_rank=hit.vector_rank,
        keyword_rank=hit.keyword_rank,
    )


@dataclass(slots=True)
class _Turn:
    start: NotebookChatStart
    messages: list[ChatMessage]
    sources: list[SourceOut] = field(default_factory=list)
    # The source filter this turn ran under; stored so the next turn can detect
    # a change. None means every source.
    document_ids: list[str] | None = None


async def _begin_turn(
    session: SessionDep,
    user_id: uuid.UUID,
    notebook_id: uuid.UUID,
    payload: NotebookChatRequest,
) -> _Turn:
    """Retrieve, persist the question and a placeholder reply, build the prompt."""
    notebook = await session.scalar(
        select(Notebook).where(Notebook.id == notebook_id, Notebook.user_id == user_id)
    )
    if notebook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")

    try:
        model = await get_registry().resolve_chat_model(payload.model)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    question = payload.content.strip()

    hits = await hybrid_search(
        session,
        query=question,
        notebook_id=notebook_id,
        document_ids=payload.document_ids,
        limit=RETRIEVAL_LIMIT,
    )

    if payload.conversation_id is None:
        conversation = Conversation(
            user_id=user_id,
            notebook_id=notebook_id,
            title=title_from(question),
            model=model,
        )
        session.add(conversation)
        await session.flush()
    else:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == user_id,
                Conversation.notebook_id == notebook_id,
            )
        )
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        conversation.model = model

    existing = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.position)
        )
    )
    next_position = (existing[-1].position + 1) if existing else 0

    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=question,
        position=next_position,
    )
    session.add(user_message)

    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        position=next_position + 1,
        model=model,
    )
    session.add(assistant)
    await session.commit()

    sources = [_source_out(i, hit) for i, hit in enumerate(hits, start=1)]

    # Replaying earlier turns is only safe while the same sources are
    # searchable. Once the user unticks a document, a prior answer may assert
    # something this turn's sources no longer support, and the model will copy
    # it forward — producing a claim, and previously a citation, with nothing
    # behind it. Stripping markers helps; dropping the history removes the
    # affordance entirely, which a prompt instruction alone cannot guarantee.
    current_filter = _filter_key(payload.document_ids)
    last_assistant = next(
        (m for m in reversed(existing) if m.role == "assistant" and m.content.strip()),
        None,
    )
    previous_filter = (last_assistant.meta or {}).get("document_ids") if last_assistant else None
    sources_changed = last_assistant is not None and previous_filter != current_filter

    history = (
        []
        if sources_changed
        else [
            ChatMessage(role=m.role, content=m.content)
            for m in existing[-HISTORY_TURNS:]
            if m.content.strip()
        ]
    )
    if sources_changed:
        logger.info("notebook chat %s: source filter changed, dropping history", conversation.id)

    return _Turn(
        document_ids=current_filter,
        start=NotebookChatStart(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant.id,
            model=model,
            title=conversation.title,
        ),
        messages=build_messages(question, hits, history),
        sources=sources,
    )


def _filter_key(document_ids: list[uuid.UUID] | None) -> list[str] | None:
    """A comparable form of the source filter. None means every source."""
    return sorted(str(d) for d in document_ids) if document_ids is not None else None


async def _save_reply(
    message_id: uuid.UUID,
    content: str,
    sources: list[SourceOut],
    stop_reason: str | None,
    usage: dict[str, Any],
    error: str | None,
    document_ids: list[str] | None = None,
) -> None:
    """Persist the reply with the sources it was grounded in.

    Sources are stored on the message so reopening a conversation shows the
    same citations. Re-retrieving later could return different passages and
    silently make old citation numbers point somewhere else.
    """
    used = set(cited_indices(content, len(sources)))
    # The live stream is cleaned by the frontend as it renders; this cleans
    # what is stored, so re-reading a conversation — or any client that is not
    # our own — never sees a marker pointing at no source.
    content = strip_invalid_citations(content, len(sources))

    async with SessionLocal() as session:
        row = await session.get(Message, message_id)
        if row is None:
            return
        row.content = content
        row.meta = {
            "stop_reason": stop_reason,
            "usage": usage,
            "sources": [s.model_dump(mode="json") for s in sources],
            "cited": sorted(used),
            # Which sources were searchable for this turn, so a later turn can
            # tell whether the ground under the conversation has shifted.
            "document_ids": document_ids,
            **({"error": error} if error else {}),
        }
        await session.commit()


async def _event_stream(turn: _Turn) -> AsyncIterator[str]:
    model = turn.start.model
    pieces: list[str] = []
    stop_reason: str | None = None
    usage: dict[str, Any] = {}
    error: str | None = None

    try:
        yield _sse("start", turn.start)
        yield _sse("sources", SourcesEvent(sources=turn.sources))

        # Nothing retrieved: say so rather than asking the model to answer
        # from nothing, which is exactly when it invents an answer.
        #
        # Deliberately not an early return. Returning here would run `finally`
        # and terminate the generator, skipping the trailing `done` — and the
        # client uses that terminal event to tell a clean finish from a dropped
        # connection, so the user saw a red "connection closed" error instead
        # of this message.
        if not turn.sources:
            pieces.append(NO_SOURCES_MESSAGE)
            yield _sse("token", TokenEvent(text=NO_SOURCES_MESSAGE))
            stop_reason = "no_sources"
        else:
            provider = await get_registry().provider_for(model)
            async for chunk in provider.chat_stream(turn.messages, model=model, think=False):
                if chunk.type == "text":
                    pieces.append(chunk.text)
                    yield _sse("token", TokenEvent(text=chunk.text))
                elif chunk.type == "error":
                    error = chunk.text
                    break
                elif chunk.type == "done":
                    stop_reason = chunk.stop_reason
                    usage = chunk.usage
                    break
    except ProviderError as exc:
        error = str(exc)
    except Exception as exc:  # pragma: no cover - unexpected provider failure
        logger.exception("notebook chat stream failed")
        error = f"{type(exc).__name__}: {exc}"
    except BaseException:
        stop_reason = CANCELLED  # client disconnected mid-stream
        raise
    finally:
        # Shielded: after a disconnect the surrounding scope is already cancelled.
        with anyio.CancelScope(shield=True):
            await _save_reply(
                turn.start.assistant_message_id,
                "".join(pieces),
                turn.sources,
                stop_reason,
                usage,
                error,
                turn.document_ids,
            )

    if error:
        yield _sse("error", ErrorEvent(message=error))
    else:
        yield _sse("done", DoneEvent(stop_reason=stop_reason, usage=usage))


@router.post("/{notebook_id}/chat/stream")
async def notebook_chat_stream(
    notebook_id: uuid.UUID,
    payload: NotebookChatRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> StreamingResponse:
    """Ask a question of a notebook's sources and stream a cited answer."""
    if not payload.content.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Question cannot be empty")

    turn = await _begin_turn(session, user_id, notebook_id, payload)
    return StreamingResponse(
        _event_stream(turn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{notebook_id}/conversations")
async def list_notebook_conversations(
    notebook_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> list[dict[str, Any]]:
    """Conversations belonging to this notebook, newest first."""
    exists = await session.scalar(
        select(func.count(Notebook.id)).where(
            Notebook.id == notebook_id, Notebook.user_id == user_id
        )
    )
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notebook not found")

    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.notebook_id == notebook_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "model": c.model,
            "updated_at": c.updated_at.isoformat(),
        }
        for c in rows
    ]
