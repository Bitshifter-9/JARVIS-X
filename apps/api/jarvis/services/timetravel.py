"""Time-travel reconstruction (#30): "what was I working on last Tuesday?" — a day rebuilt
from everything captured that day: the tasks you finished, the deadlines, the mail and
messages, the notes you kept, and (where a device was sampling) the apps you spent time in.
Deterministic, no model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.domain import Task
from jarvis.db.models.ops import Memory
from jarvis.db.models.source import SourceObject
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession


async def reconstruct_day(
    session: AsyncSession, user_id: uuid.UUID, day: date, *, tz: str = "UTC"
) -> dict[str, Any]:
    """Everything that touched a given local day, grouped — the shape of that day."""
    from jarvis.services import activity

    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = start + timedelta(days=1)

    done = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user_id,
                Task.completed_at >= start,
                Task.completed_at < end,
            )
        )
    ).all()
    due = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user_id,
                Task.due_at >= start,
                Task.due_at < end,
            )
        )
    ).all()
    messages = (
        await session.scalars(
            select(SourceObject)
            .where(
                SourceObject.user_id == user_id,
                SourceObject.occurred_at >= start,
                SourceObject.occurred_at < end,
            )
            .order_by(SourceObject.occurred_at)
            .limit(50)
        )
    ).all()
    notes = (
        await session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.invalidated_at.is_(None),
                Memory.created_at >= start,
                Memory.created_at < end,
                or_(Memory.kind == "semantic", Memory.kind == "episodic"),
            )
            .order_by(Memory.created_at)
            .limit(30)
        )
    ).all()
    chats = await session.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= start,
            ChatMessage.created_at < end,
        )
        .limit(1)
    )
    apps = await activity.summary(session, user_id, start, end)

    return {
        "date": day.isoformat(),
        "empty": not (done or due or messages or notes or apps or chats),
        "done": [{"title": t.title} for t in done],
        "deadlines": [{"title": t.title} for t in due],
        "messages": [
            {"from": m.author, "subject": m.title, "provider": m.provider} for m in messages
        ],
        "notes": [{"content": m.content, "kind": m.kind} for m in notes],
        "apps": apps[:8],
        "chatted": chats is not None,
    }
