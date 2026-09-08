"""Focus analytics (#36): deep-work time, the apps that pull you away, and your best focus
window — over the activity samples already collected (opt-in, app+title, never pixels). No
model. Empty until a device is sampling (Android UsageStats, or the Mac sampler #3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def focus_analytics(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = 7,
    tz: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deep-work vs distraction minutes and the top distraction sources for the window."""
    from jarvis.services import activity
    from jarvis.services.rhythm import rhythm

    moment = now or datetime.now(UTC)
    start = moment - timedelta(days=days)
    apps = await activity.summary(session, user_id, start, moment)
    if not apps:
        return {"enough_data": False}

    terms = activity.distracting_terms()

    def distracting(app: str) -> bool:
        low = app.lower()
        return any(t in low for t in terms)

    distraction_minutes = sum(a["minutes"] for a in apps if distracting(a["app"]))
    deep_minutes = sum(a["minutes"] for a in apps if not distracting(a["app"]))
    distractions = [
        {"app": a["app"], "minutes": a["minutes"]} for a in apps if distracting(a["app"])
    ][:5]

    r = await rhythm(session, user_id, tz=tz)
    return {
        "enough_data": True,
        "days": days,
        "deep_minutes": round(deep_minutes),
        "distraction_minutes": round(distraction_minutes),
        "top_apps": apps[:5],
        "distractions": distractions,
        "best_window": r.get("best_window") if r.get("enough_data") else None,
    }
