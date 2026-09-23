"""Conversation and message CRUD."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUserId, SessionDep
from app.llm import get_registry
from app.models.chat import DEFAULT_TITLE, Conversation, Message, title_from
from app.schemas.chat import (
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationPatch,
    MessageOut,
    TruncateRequest,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        position=message.position,
        model=message.model,
        metadata=message.meta,
        created_at=message.created_at,
    )


async def load_conversation(
    session: SessionDep, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation:
    """Fetch a conversation scoped to its owner.

    Scoping every read by user_id now means M9 changes nothing here.
    """
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session: SessionDep, user_id: CurrentUserId, limit: int = 100
) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    return list(result)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, session: SessionDep, user_id: CurrentUserId
) -> Conversation:
    model = await get_registry().resolve_chat_model(payload.model)
    conversation = Conversation(
        user_id=user_id,
        title=payload.title or DEFAULT_TITLE,
        model=model,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> ConversationDetailOut:
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        model=conversation.model,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_to_message_out(m) for m in conversation.messages],
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def patch_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationPatch,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Conversation:
    conversation = await load_conversation(session, conversation_id, user_id)

    if payload.title is not None:
        conversation.title = payload.title
    if payload.model is not None:
        # Validate against what is actually servable, so a stale dropdown
        # cannot persist a model that no longer exists.
        conversation.model = await get_registry().resolve_chat_model(payload.model)

    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user_id: CurrentUserId
) -> None:
    conversation = await load_conversation(session, conversation_id, user_id)
    await session.delete(conversation)
    await session.commit()


@router.post("/{conversation_id}/truncate", response_model=list[MessageOut])
async def truncate_conversation(
    conversation_id: uuid.UUID,
    payload: TruncateRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[MessageOut]:
    """Drop messages from `position` onward and return what remains.

    Regenerate truncates at the last assistant turn; edit-and-resend truncates
    at the edited user turn.
    """
    conversation = await load_conversation(session, conversation_id, user_id)

    if payload.position == 0:
        first = await session.scalar(
            select(Message).where(Message.conversation_id == conversation_id, Message.position == 0)
        )
        # An auto-title leaves with its first message; a title the user set stays.
        if first is not None and conversation.title == title_from(first.content):
            conversation.title = DEFAULT_TITLE

    await session.execute(
        delete(Message).where(
            Message.conversation_id == conversation_id,
            Message.position >= payload.position,
        )
    )
    await session.commit()

    remaining = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.position)
    )
    return [_to_message_out(m) for m in remaining]


async def next_position(session: SessionDep, conversation_id: uuid.UUID) -> int:
    """Next slot in a conversation.

    Positions are explicit because ordering by created_at breaks when two rows
    land in the same transaction and share a timestamp.
    """
    highest = await session.scalar(
        select(func.max(Message.position)).where(Message.conversation_id == conversation_id)
    )
    return 0 if highest is None else highest + 1
