"""What Jarvis remembers about you — visible, and deletable.

Memory is only useful if it is inspectable: a fact learned from a misheard sentence
should be one tap from gone. Deleting invalidates the row (the audit trail keeps the
belief's history); nothing invalidated is ever retrieved again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.errors import NotFound
from jarvis.db.models.ops import Memory

router = APIRouter(prefix="/v1/memories", tags=["memories"])


@router.get("")
async def list_memories(
    user: CurrentUser, session: SessionDep, limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user.id, Memory.invalidated_at.is_(None))
            .order_by(Memory.created_at.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [
        {
            "id": str(m.id),
            "kind": m.kind,
            "content": m.content,
            "importance": m.importance,
            "source": (m.provenance or {}).get("source"),
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.delete("/{memory_id}")
async def forget(memory_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    memory = await session.get(Memory, memory_id)
    if memory is None or memory.user_id != user.id:
        raise NotFound("Memory")
    memory.invalidated_at = datetime.now(UTC)
    await session.flush()
    return {"forgotten": True}
