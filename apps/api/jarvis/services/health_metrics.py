"""Energy/health correlation (#38): connect sleep and steps, and learn what actually moves your
day — correlate each day's health metric with that day's productivity (focus minutes + tasks
done). Deterministic (Pearson), no model. Nothing here judges; it just shows the relationship.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core.ids import uuid7
from jarvis.db.models.domain import HealthSample, Task, WorkSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 2)


async def record_health(
    session: AsyncSession, user_id: uuid.UUID, samples: list[dict[str, Any]]
) -> int:
    """Upsert daily health rows (one per day)."""
    stored = 0
    for raw in samples[:120]:
        try:
            day = datetime.fromisoformat(str(raw["day"]))
        except (KeyError, ValueError):
            continue
        values = {
            "id": uuid7(), "user_id": user_id, "day": day,
            "steps": raw.get("steps"), "sleep_minutes": raw.get("sleep_minutes"),
            "active_minutes": raw.get("active_minutes"),
        }
        stmt = pg_insert(HealthSample).values(values).on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={"steps": values["steps"], "sleep_minutes": values["sleep_minutes"],
                  "active_minutes": values["active_minutes"]},
        )
        await session.execute(stmt)
        stored += 1
    await session.flush()
    return stored


def _label(metric: str) -> str:
    return {"sleep_minutes": "sleep", "steps": "steps", "active_minutes": "activity"}.get(
        metric, metric
    )


async def correlation(
    session: AsyncSession, user_id: uuid.UUID, *, days: int = 45, tz: str = "UTC"
) -> dict[str, Any]:
    """Correlate each health metric with that day's productivity (focus mins + tasks done)."""
    zone = ZoneInfo(tz)
    health = (
        await session.scalars(select(HealthSample).where(HealthSample.user_id == user_id))
    ).all()
    if len(health) < 4:
        return {"enough_data": False}

    # Productivity per local day: focus minutes + 20 per completed task.
    prod: dict[date, float] = defaultdict(float)
    focus = (
        await session.scalars(
            select(WorkSession).where(
                WorkSession.user_id == user_id, WorkSession.source == "focus"
            )
        )
    ).all()
    for w in focus:
        if w.started_at:
            prod[w.started_at.astimezone(zone).date()] += w.active_minutes or 0
    done = (
        await session.scalars(
            select(Task.completed_at).where(
                Task.user_id == user_id, Task.status == "done",
                Task.completed_at.is_not(None),
            )
        )
    ).all()
    for t in done:
        if t:
            prod[t.astimezone(zone).date()] += 20

    results = []
    for metric in ("sleep_minutes", "steps", "active_minutes"):
        xs, ys = [], []
        for h in health:
            val = getattr(h, metric)
            d = h.day.astimezone(zone).date()
            if val is not None and d in prod:
                xs.append(float(val))
                ys.append(prod[d])
        r = _pearson(xs, ys)
        if r is None:
            continue
        results.append({
            "metric": _label(metric), "correlation": r, "n": len(xs),
            "insight": (f"More {_label(metric)} tends to go with a more productive day."
                        if r > 0.3 else
                        f"Less {_label(metric)} tends to go with a more productive day."
                        if r < -0.3 else
                        f"{_label(metric).capitalize()} and your productivity look unrelated."),
        })
    return {"enough_data": bool(results), "correlations": results}
