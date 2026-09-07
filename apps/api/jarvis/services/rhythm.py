"""Rhythm model (#15): when you actually focus, and when you slump — your energy curve by
hour of day, so JARVIS can schedule and nudge when you're receptive, not when you're spent.

Deterministic and free: the hours your focus sessions ran and your tasks got done, over the
last few weeks, bucketed by local hour. No model, no new writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.domain import Task, WorkSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MIN_SAMPLES = 8  # below this the curve is noise, so we don't pretend to know


def _best_window(by_hour: list[int], span: int = 3) -> tuple[int, int] | None:
    """The contiguous `span`-hour window with the most activity, as (start_hour, end_hour)."""
    if not any(by_hour):
        return None
    best_start, best_sum = 0, -1
    for start in range(24):
        total = sum(by_hour[(start + i) % 24] for i in range(span))
        if total > best_sum:
            best_start, best_sum = start, total
    return best_start, (best_start + span) % 24


async def rhythm(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    tz: str = "UTC",
    weeks: int = 8,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The user's productive hours: a 24-bucket curve, the peak hour, and the best window."""
    moment = now or datetime.now(UTC)
    since = moment - timedelta(weeks=weeks)
    zone = ZoneInfo(tz)

    focus = (
        await session.scalars(
            select(WorkSession.started_at).where(
                WorkSession.user_id == user_id,
                WorkSession.source == "focus",
                WorkSession.started_at >= since,
            )
        )
    ).all()
    done = (
        await session.scalars(
            select(Task.completed_at).where(
                Task.user_id == user_id,
                Task.status == "done",
                Task.completed_at.is_not(None),
                Task.completed_at >= since,
            )
        )
    ).all()

    by_hour = [0] * 24
    for t in [*focus, *done]:
        if t:
            by_hour[t.astimezone(zone).hour] += 1
    samples = sum(by_hour)
    if samples < _MIN_SAMPLES:
        return {"enough_data": False, "samples": samples, "by_hour": by_hour}

    window = _best_window(by_hour)
    peak_hour = max(range(24), key=lambda h: by_hour[h])
    return {
        "enough_data": True,
        "samples": samples,
        "by_hour": by_hour,
        "peak_hour": peak_hour,
        "best_window": {"start": window[0], "end": window[1]} if window else None,
    }


if __name__ == "__main__":  # self-check of the window/peak maths
    by = [0] * 24
    for h in (9, 10, 10, 11, 11, 11, 14, 20):  # a clear morning peak at 11
        by[h] += 1
    assert _best_window(by) == (9, 12), _best_window(by)
    assert max(range(24), key=lambda h: by[h]) == 11
    print("ok")
