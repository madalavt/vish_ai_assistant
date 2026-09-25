"""Chat request and response shapes."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    position: int
    model: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    # Set for notebook grounded-chat threads, null for chat-tab conversations.
    notebook_id: uuid.UUID | None = None
    model: str
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ConversationCreate(BaseModel):
    title: str | None = None
    model: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = None


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    # None means "regenerate": re-run the last user turn instead of adding one.
    content: str | None = None
    model: str | None = None
    think: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class TruncateRequest(BaseModel):
    """Delete messages from `position` onward.

    Powers both regenerate (drop the last assistant turn) and edit-and-resend
    (drop the edited user turn and everything after it).
    """

    position: int = Field(ge=0)


# ---------------------------------------------------------------------------
# SSE event payloads. The frontend mirrors these in lib/chat.ts — keep in sync.
# ---------------------------------------------------------------------------

SSEEvent = Literal["start", "token", "thinking", "done", "error"]


class StartEvent(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID | None
    assistant_message_id: uuid.UUID
    model: str
    title: str


class TokenEvent(BaseModel):
    text: str


class DoneEvent(BaseModel):
    stop_reason: str | None
    usage: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    message: str
