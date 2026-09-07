"""Chat history — the conversation lives with the account, not the device.

The same user talks to Jarvis from the Mac and the phone; storing turns server-side
is what makes both surfaces show one conversation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    """One thread of chat. Titled after the first exchange; archived, never deleted
    silently — the timeline may cite what was said."""

    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="New chat")
    persona: Mapped[str | None] = mapped_column(String(64))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "chat_messages"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # user | assistant
    # Where the reply came from and what it triggered: shown as chips in the app.
    meta: Mapped[dict | None] = mapped_column(JSONB)
    content: Mapped[str] = mapped_column(Text, nullable=False)
