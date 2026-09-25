"""Conversations and messages."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin

ROLES = ("user", "assistant", "system")

DEFAULT_TITLE = "New conversation"
TITLE_MAX_CHARS = 60


def title_from(content: str) -> str:
    """First line, clipped. Good enough, and no extra model call."""
    first_line = content.strip().splitlines()[0] if content.strip() else DEFAULT_TITLE
    if len(first_line) <= TITLE_MAX_CHARS:
        return first_line
    return first_line[: TITLE_MAX_CHARS - 1].rstrip() + "…"


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default=DEFAULT_TITLE)
    # Set when this conversation belongs to a notebook's grounded chat. Null for
    # ordinary chat-tab conversations, so both kinds share one table and all the
    # message handling already built for them.
    notebook_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Remembered per conversation so switching models does not leak across threads.
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.position",
    )

    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Monotonic within a conversation; ordering by created_at breaks on identical timestamps.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which model produced an assistant turn (null for user turns).
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Tool calls, citations, token counts, stop_reason. JSONB, not a second store.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_position", "conversation_id", "position", unique=True),
    )
