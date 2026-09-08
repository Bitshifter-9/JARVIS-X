"""Overnight agent (#45): within standing permissions, prepare while you sleep — catch new
promises, derive mail insights, and pre-warm tomorrow's brief — then report it in the morning.
Every step is one the system already does on request; nothing effectful runs without approval.
Opt-in (``overnight_agent_enabled``), once per night, auditable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.ops import AuditLog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _already_swept_today(session: AsyncSession, user_id: uuid.UUID) -> bool:
    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.action == "overnight.swept",
            AuditLog.created_at >= func.date_trunc("day", func.now()),
        )
    )
    return bool(count)


async def overnight_sweep(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC", now: datetime | None = None
) -> dict[str, Any] | None:
    """Run the nightly prep for one user (only in the local small hours, once). Returns the
    report, or None if it wasn't the right time / already done."""
    moment = now or datetime.now(UTC)
    local_hour = moment.astimezone(ZoneInfo(tz)).hour
    if not (1 <= local_hour <= 5):
        return None
    if await _already_swept_today(session, user_id):
        return None

    from jarvis.services.commitment.service import scan_commitments
    from jarvis.services.insights.service import derive_insights
    from jarvis.services.modules import ModuleService

    caught = await scan_commitments(session, user_id)
    written = await derive_insights(session, user_id)
    brief = await ModuleService(session).morning_brief(user_id, now=moment)

    report = {
        "commitments_caught": caught,
        "insights_written": written,
        "at_risk": len(brief.at_risk),
        "headline": brief.headline,
        "at": moment.isoformat(),
    }
    session.add(AuditLog(
        user_id=user_id, actor="system", action="overnight.swept",
        subject_type="overnight", detail=report,
    ))
    await session.flush()
    return report


async def last_report(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any] | None:
    """The most recent overnight sweep report, for the morning."""
    row = await session.scalar(
        select(AuditLog)
        .where(AuditLog.user_id == user_id, AuditLog.action == "overnight.swept")
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    return row.detail if row else None
