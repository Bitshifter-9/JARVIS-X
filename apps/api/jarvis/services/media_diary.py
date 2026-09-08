"""Media diary (#6): what you watched and read, turned into recall-able knowledge by a
one-line "why it mattered". Built on the reading/watching log (#4) — no new capture — plus a
takeaway you attach. The note also lands in the searchable text, so life-search finds it.
"""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.db.models.source import SourceObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _out(o: SourceObject) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "title": o.title,
        "url": o.url,
        "kind": o.kind,
        "when": o.occurred_at.isoformat() if o.occurred_at else o.created_at.isoformat(),
        "note": (o.raw or {}).get("note"),
    }


async def media_diary(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 40
) -> list[dict[str, Any]]:
    """Your recent consumption (from the reading/watching log), newest first, with takeaways."""
    rows = (
        await session.scalars(
            select(SourceObject)
            .where(SourceObject.user_id == user_id, SourceObject.provider == "reading")
            .order_by(SourceObject.occurred_at.desc().nulls_last())
            .limit(limit)
        )
    ).all()
    return [_out(o) for o in rows]


async def set_note(
    session: AsyncSession, user_id: uuid.UUID, source_id: uuid.UUID, note: str
) -> dict[str, Any] | None:
    """Attach a "why it mattered" to something you consumed — and make it searchable."""
    obj = await session.get(SourceObject, source_id)
    if obj is None or obj.user_id != user_id or obj.provider != "reading":
        return None
    raw = dict(obj.raw or {})
    raw["note"] = note[:1000]
    obj.raw = raw
    # Fold the takeaway into the searchable text so life-search recalls it later.
    base = (obj.title or "")[:200]
    obj.excerpt = f"{base} — {note}"[:8000]
    await session.flush()
    return _out(obj)
