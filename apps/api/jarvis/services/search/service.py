"""One keyword search over the user's whole captured world — mail/Slack, deadlines, chat
history, and memories — ranked by recency.

Deliberately keyword-only (Postgres ``ILIKE``): it is free, needs no embeddings, and is
the cheap-first tier the second-brain roadmap calls for. Semantic re-ranking with the
memory embeddings is the optimisation to layer on later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.domain import Task
from jarvis.db.models.ops import Memory
from jarvis.db.models.source import SourceObject
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession


def _like(q: str) -> str:
    # Escape ILIKE wildcards so a query of "50%" doesn't match everything.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str | None, q: str, *, width: int = 140) -> str:
    if not text:
        return ""
    low = text.lower()
    i = low.find(q.lower())
    if i < 0:
        return text[:width].strip()
    start = max(0, i - width // 3)
    end = min(len(text), i + len(q) + width)
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


async def life_search(
    session: AsyncSession, user_id: uuid.UUID, query: str, *, limit: int = 30
) -> list[dict[str, Any]]:
    q = query.strip()
    if len(q) < 2:
        return []
    pat = _like(q)
    per = max(5, limit)

    results: list[dict[str, Any]] = []

    mail = (
        await session.scalars(
            select(SourceObject)
            .where(
                SourceObject.user_id == user_id,
                or_(SourceObject.title.ilike(pat), SourceObject.excerpt.ilike(pat)),
            )
            .order_by(SourceObject.occurred_at.desc().nulls_last())
            .limit(per)
        )
    ).all()
    for m in mail:
        results.append(
            {
                "type": "mail" if m.provider in ("gmail", "slack") else m.kind,
                "title": m.title or "(no subject)",
                "snippet": _snippet(m.excerpt, q),
                "who": m.author,
                "when": _iso(m.occurred_at or m.created_at),
                "route": "insights",
                "id": str(m.id),
            }
        )

    tasks = (
        await session.scalars(
            select(Task)
            .where(Task.user_id == user_id, Task.title.ilike(pat))
            .order_by(Task.due_at.desc().nulls_last(), Task.created_at.desc())
            .limit(per)
        )
    ).all()
    for t in tasks:
        results.append(
            {
                "type": "deadline",
                "title": t.title,
                "snippet": _snippet(t.evidence_span, q),
                "when": _iso(t.due_at or t.created_at),
                "route": "goals",
                "id": str(t.id),
            }
        )

    messages = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.content.ilike(pat))
            .order_by(ChatMessage.created_at.desc())
            .limit(per)
        )
    ).all()
    for msg in messages:
        results.append(
            {
                "type": "chat",
                "title": "You" if msg.role == "user" else "Jarvis",
                "snippet": _snippet(msg.content, q),
                "when": _iso(msg.created_at),
                "route": "jarvis",
                "id": str(msg.conversation_id) if msg.conversation_id else str(msg.id),
            }
        )

    memories = (
        await session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.invalidated_at.is_(None),
                Memory.content.ilike(pat),
            )
            .order_by(Memory.created_at.desc())
            .limit(per)
        )
    ).all()
    for mem in memories:
        results.append(
            {
                "type": "memory",
                "title": "Remembered",
                "snippet": _snippet(mem.content, q),
                "when": _iso(mem.created_at),
                "route": "settings",
                "id": str(mem.id),
            }
        )

    # Merge everything, newest first, and cap.
    results.sort(key=lambda r: r["when"] or "", reverse=True)
    return results[:limit]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
