"""Streaming chat.

Wire format is plain SSE with our own event names (`start`, `token`,
`thinking`, `done`, `error`) rather than the Vercel AI SDK's data-stream
protocol, which a Python backend has to chase. `frontend/lib/chat.ts` mirrors
these shapes.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import CurrentUserId, SessionDep
from app.llm import ChatMessage, ProviderError, get_registry
from app.models.chat import Conversation, Message
from app.schemas.chat import ChatRequest, DoneEvent, ErrorEvent, StartEvent, TokenEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

TITLE_MAX_CHARS = 60


def _sse(event: str, payload: BaseModel | dict) -> str:
    data = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


def _title_from(content: str) -> str:
    """First line, clipped. Good enough, and no extra model call."""
    first_line = content.strip().splitlines()[0] if content.strip() else "New conversation"
    if len(first_line) <= TITLE_MAX_CHARS:
        return first_line
    return first_line[: TITLE_MAX_CHARS - 1].rstrip() + "…"


async def _event_stream(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str | None,
    model: str,
    think: bool,
    temperature: float | None,
) -> AsyncIterator[str]:
    """Persist the turn, stream the reply, then persist the reply.

    This opens its own session on purpose. A StreamingResponse body runs *after*
    the endpoint returns, by which time a dependency-injected session is already
    closed — using one here would fail on the first write.
    """
    registry = get_registry()

    async with SessionLocal() as session:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )
        if conversation is None:
            yield _sse("error", ErrorEvent(message="Conversation not found"))
            return

        existing = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.position)
            )
        )

        user_message: Message | None = None
        if content is not None:
            user_message = Message(
                conversation_id=conversation_id,
                role="user",
                content=content,
                position=(existing[-1].position + 1) if existing else 0,
            )
            session.add(user_message)
            if not existing:
                conversation.title = _title_from(content)
            existing.append(user_message)

        if not existing:
            yield _sse("error", ErrorEvent(message="Nothing to send"))
            return

        # Placeholder row so the client has a stable id to stream into and the
        # turn survives a mid-stream disconnect.
        assistant = Message(
            conversation_id=conversation_id,
            role="assistant",
            content="",
            position=existing[-1].position + 1,
            model=model,
        )
        session.add(assistant)
        conversation.model = model
        await session.commit()

        history = [ChatMessage(role=m.role, content=m.content) for m in existing]
        yield _sse(
            "start",
            StartEvent(
                conversation_id=conversation_id,
                user_message_id=user_message.id if user_message else None,
                assistant_message_id=assistant.id,
                model=model,
                title=conversation.title,
            ),
        )
        assistant_id = assistant.id

    # Stream outside the write session so a slow model does not hold a
    # connection from the pool for the whole generation.
    pieces: list[str] = []
    thinking: list[str] = []
    stop_reason: str | None = None
    usage: dict = {}
    error: str | None = None

    try:
        provider = await registry.provider_for(model)
        async for chunk in provider.chat_stream(
            history,
            model=model,
            think=think,
            temperature=temperature,
        ):
            if chunk.type == "text":
                pieces.append(chunk.text)
                yield _sse("token", TokenEvent(text=chunk.text))
            elif chunk.type == "thinking":
                thinking.append(chunk.text)
                yield _sse("thinking", TokenEvent(text=chunk.text))
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
        logger.exception("chat stream failed")
        error = f"{type(exc).__name__}: {exc}"

    # Persist whatever arrived, including on failure: a partial answer is more
    # useful than a lost one, and the error is recorded alongside it.
    async with SessionLocal() as session:
        row = await session.get(Message, assistant_id)
        if row is not None:
            row.content = "".join(pieces)
            row.meta = {
                "stop_reason": stop_reason,
                "usage": usage,
                **({"thinking": "".join(thinking)} if thinking else {}),
                **({"error": error} if error else {}),
            }
            await session.commit()

    if error:
        yield _sse("error", ErrorEvent(message=error))
    else:
        yield _sse("done", DoneEvent(stop_reason=stop_reason, usage=usage))


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> StreamingResponse:
    """Send a message (or regenerate, when `content` is null) and stream the reply."""
    if payload.content is not None and not payload.content.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Message cannot be empty")

    try:
        model = await get_registry().resolve_chat_model(payload.model)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    # Validate up front so a bad request is a real HTTP error, not an SSE
    # event the client has to special-case.
    if payload.conversation_id is None:
        if payload.content is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A new conversation needs a message",
            )
        conversation = Conversation(user_id=user_id, title="New conversation", model=model)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        conversation_id = conversation.id
    else:
        exists = await session.scalar(
            select(Conversation.id).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        conversation_id = payload.conversation_id

    return StreamingResponse(
        _event_stream(
            conversation_id,
            user_id,
            payload.content,
            model,
            payload.think,
            payload.temperature,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream if one is ever put in front.
            "X-Accel-Buffering": "no",
        },
    )
