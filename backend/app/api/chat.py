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
from dataclasses import dataclass
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.deps import CurrentUserId, SessionDep
from app.llm import ChatMessage, ProviderError, get_registry
from app.models.chat import DEFAULT_TITLE, Conversation, Message, title_from
from app.schemas.chat import ChatRequest, DoneEvent, ErrorEvent, StartEvent, TokenEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# The stop_reason recorded when the client disconnects mid-stream, as Stop does.
CANCELLED = "cancelled"


def _sse(event: str, payload: BaseModel | dict) -> str:
    data = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"


async def _resolve_model(requested: str | None) -> str:
    try:
        return await get_registry().resolve_chat_model(requested)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@dataclass(slots=True)
class _Turn:
    start: StartEvent
    history: list[ChatMessage]


async def _begin_turn(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    content: str | None,
    requested_model: str | None,
) -> _Turn:
    """Commit the user message and reply placeholder up front, so failures are HTTP errors."""
    if conversation_id is None:
        if content is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A new conversation needs a message"
            )
        model = await _resolve_model(requested_model)
        conversation = Conversation(user_id=user_id, title=title_from(content), model=model)
        session.add(conversation)
        await session.flush()
        existing: list[Message] = []
    else:
        # Locked so concurrent sends take turns choosing message positions.
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .with_for_update()
        )
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        model = await _resolve_model(requested_model or conversation.model)
        existing = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.position)
            )
        )

    user_message: Message | None = None
    if content is not None:
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=content,
            position=(existing[-1].position + 1) if existing else 0,
        )
        session.add(user_message)
        # Never replace a title the user chose.
        if conversation.title == DEFAULT_TITLE:
            conversation.title = title_from(content)
        existing.append(user_message)

    # Regenerate re-runs the last user turn, so the old reply must be truncated first.
    if not existing or existing[-1].role != "user":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nothing to regenerate")

    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        position=existing[-1].position + 1,
        model=model,
    )
    session.add(assistant)
    conversation.model = model
    # An otherwise unchanged row emits no UPDATE, so onupdate alone never fires.
    conversation.updated_at = func.now()
    await session.commit()

    return _Turn(
        start=StartEvent(
            conversation_id=conversation.id,
            user_message_id=user_message.id if user_message else None,
            assistant_message_id=assistant.id,
            model=model,
            title=conversation.title,
        ),
        # Skips empty replies (failed, stopped early, or still streaming elsewhere).
        history=[
            ChatMessage(role=m.role, content=m.content)
            for m in existing
            if m.content or m.role != "assistant"
        ],
    )


async def _save_reply(
    message_id: uuid.UUID,
    content: str,
    thinking: str,
    stop_reason: str | None,
    usage: dict[str, Any],
    error: str | None,
) -> None:
    async with SessionLocal() as session:
        row = await session.get(Message, message_id)
        if row is None:  # the conversation was deleted mid-stream
            return
        row.content = content
        row.meta = {
            "stop_reason": stop_reason,
            "usage": usage,
            **({"thinking": thinking} if thinking else {}),
            **({"error": error} if error else {}),
        }
        await session.commit()


async def _event_stream(turn: _Turn, think: bool, temperature: float | None) -> AsyncIterator[str]:
    """Stream the reply, holding no DB connection, then save it into the placeholder."""
    model = turn.start.model
    pieces: list[str] = []
    thinking: list[str] = []
    stop_reason: str | None = None
    usage: dict[str, Any] = {}
    error: str | None = None

    try:
        yield _sse("start", turn.start)
        registry = get_registry()
        provider = await registry.provider_for(model)
        # Models without a thinking mode reject the flag (Ollama answers 400).
        think = think and any(
            m.id == model and m.supports_thinking for m in await registry.list_models()
        )
        async for chunk in provider.chat_stream(
            turn.history,
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
    except BaseException:
        stop_reason = CANCELLED  # the client disconnected mid-stream
        raise
    finally:
        # Keep whatever arrived. Shielded: after a disconnect the scope is already cancelled.
        with anyio.CancelScope(shield=True):
            await _save_reply(
                turn.start.assistant_message_id,
                "".join(pieces),
                "".join(thinking),
                stop_reason,
                usage,
                error,
            )

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

    turn = await _begin_turn(
        session, user_id, payload.conversation_id, payload.content, payload.model
    )
    return StreamingResponse(
        _event_stream(turn, payload.think, payload.temperature),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream if one is ever put in front.
            "X-Accel-Buffering": "no",
        },
    )
