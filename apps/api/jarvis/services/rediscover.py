"""Rediscover (#27): surface an old idea or note relevant to what you're doing right now —
serendipity on purpose. Takes your current focus (the most recent thing you told JARVIS, or
your top open task), finds a keyword-matching memory you haven't touched in a while, and
brings one back. Deterministic, no model.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.db.models.chat import ChatMessage
from jarvis.db.models.domain import Task
from jarvis.db.models.ops import Memory
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_STOP = {"the", "and", "for", "with", "that", "this", "you", "your", "have", "what", "how",
         "remind", "need", "want", "please", "about", "from", "into", "will", "can", "should"}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z']{3,}", (text or "").lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:6]


async def rediscover(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    min_age_days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One older memory relevant to your current focus, or empty if nothing fits."""
    moment = now or datetime.now(UTC)

    context = await session.scalar(
        select(ChatMessage.content)
        .where(ChatMessage.user_id == user_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    if not context:
        context = await session.scalar(
            select(Task.title)
            .where(Task.user_id == user_id, Task.status.in_(("open", "in_progress")))
            .order_by(Task.due_at.asc().nulls_last())
            .limit(1)
        )
    terms = _keywords(context or "")
    if not terms:
        return {}

    cutoff = moment - timedelta(days=min_age_days)
    clauses = [Memory.content.ilike(f"%{t}%") for t in terms]
    hit = await session.scalar(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.invalidated_at.is_(None),
            Memory.kind.in_(("semantic", "source")),
            Memory.created_at < cutoff,
            or_(*clauses),
        )
        .order_by(Memory.importance.desc(), Memory.created_at.asc())
        .limit(1)
    )
    if hit is None:
        return {}
    matched = next((t for t in terms if t in hit.content.lower()), terms[0])
    return {
        "content": hit.content,
        "kind": hit.kind,
        "age_days": (moment - hit.created_at).days,
        "because": matched,
    }


if __name__ == "__main__":  # self-check of keyword extraction
    assert "kubernetes" in _keywords("How do I scale Kubernetes today?")
    assert "the" not in _keywords("the plan for the day")
    print("ok")
