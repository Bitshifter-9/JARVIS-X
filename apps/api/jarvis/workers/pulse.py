"""Each loop writes its pulse; the status page reads it. A worker that stopped is a
row whose ``last_tick_at`` is old — visible, instead of silently absent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models.ops import WorkerHeartbeat


async def pulse(session: AsyncSession, name: str, **detail: Any) -> None:
    now = datetime.now(UTC)
    await session.execute(
        pg_insert(WorkerHeartbeat)
        .values(name=name, last_tick_at=now, detail=detail)
        .on_conflict_do_update(
            index_elements=[WorkerHeartbeat.name], set_={"last_tick_at": now, "detail": detail}
        )
    )
