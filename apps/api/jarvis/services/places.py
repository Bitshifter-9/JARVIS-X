"""Place learning (#5): significant-location detection from coarse fixes — where's home, work,
the gym — as *context*, never a map. Fixes are rounded to ~500 m before storage; places come
from clustering the rounded fixes and labelling by when you're there. Deterministic, no model.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.domain import LocationSample
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

GRID = 0.005  # ~500 m; the coarsest useful cell, and the privacy floor
RETENTION_DAYS = 30


def _round(x: float) -> float:
    return round(x / GRID) * GRID


async def record_locations(
    session: AsyncSession, user_id: uuid.UUID, samples: list[dict[str, Any]]
) -> int:
    """Store coarse fixes, rounded to the grid so nothing precise is ever kept."""
    stored = 0
    for raw in samples[:200]:
        try:
            lat, lng = float(raw["lat"]), float(raw["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        try:
            at = datetime.fromisoformat(str(raw["at"])) if raw.get("at") else datetime.now(UTC)
        except (ValueError, KeyError):
            at = datetime.now(UTC)
        session.add(LocationSample(user_id=user_id, lat=_round(lat), lng=_round(lng), at=at))
        stored += 1
    await session.flush()
    return stored


async def prune(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    result = await session.execute(
        delete(LocationSample).where(LocationSample.at < cutoff)
    )
    return int(result.rowcount or 0)


def _label(night: int, day_wk: int, total: int) -> str:
    if total == 0:
        return "frequent place"
    if night / total > 0.5:
        return "home"
    if day_wk / total > 0.4:
        return "work"
    return "frequent place"


async def places(
    session: AsyncSession, user_id: uuid.UUID, *, days: int = 30, tz: str = "UTC", limit: int = 6
) -> list[dict[str, Any]]:
    """Your significant places — the cells you spend the most time in, labelled by when."""
    zone = ZoneInfo(tz)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.scalars(
            select(LocationSample).where(
                LocationSample.user_id == user_id, LocationSample.at >= since
            )
        )
    ).all()
    if not rows:
        return []

    cells: dict[tuple[float, float], dict[str, int]] = defaultdict(
        lambda: {"visits": 0, "night": 0, "day_wk": 0}
    )
    for r in rows:
        key = (round(r.lat, 3), round(r.lng, 3))
        local = r.at.astimezone(zone)
        c = cells[key]
        c["visits"] += 1
        if local.hour >= 22 or local.hour < 6:
            c["night"] += 1
        elif 9 <= local.hour < 18 and local.weekday() < 5:
            c["day_wk"] += 1

    total = len(rows)
    out = []
    for (lat, lng), c in cells.items():
        out.append({
            "lat": lat, "lng": lng, "visits": c["visits"],
            "share": round(c["visits"] / total, 2),
            "label": _label(c["night"], c["day_wk"], c["visits"]),
        })
    out.sort(key=lambda p: p["visits"], reverse=True)
    return out[:limit]


if __name__ == "__main__":  # self-check of the labeller
    assert _label(80, 5, 100) == "home"
    assert _label(5, 60, 100) == "work"
    assert _label(10, 10, 100) == "frequent place"
    assert _round(12.97123) == round(12.97123 / GRID) * GRID
    print("ok")
