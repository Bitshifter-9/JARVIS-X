"""Peak-performance coach (from the Becoming Superhuman work): the three things the
research says actually move cognitive output, measured from data JARVIS already collects.

* **Context-switch tax** — the brain is a monotasking machine; every switch costs time and
  errors. Counted from the activity sampler, not self-reported.
* **The 3M breaks** — burnout is a stress cycle that never completes. Micro (daily), meso
  (weekly) and macro (monthly) detachment, detected as real gaps in activity.
* **Chronotype** — demanding work belongs in your biological peak, not at 5 a.m. because a
  book said so. Reuses the rhythm curve (#15).

Deterministic, no model. Empty until a device is sampling.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.ops import ActivitySample
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# A gap this long means attention actually left the machine, not that a sample was missed.
MICRO_BREAK = timedelta(minutes=10)
MESO_BREAK = timedelta(hours=2)
MACRO_BREAK = timedelta(hours=4)
# Samples land every 30 s; anything beyond this is a genuine absence, not jitter.
_SAMPLE_GAP = timedelta(minutes=3)


def switches_per_hour(apps: list[str], minutes: float) -> float:
    """How often attention changed apps, per hour. Consecutive identical samples are one
    stretch of attention; each change is a switch, and each switch is the tax."""
    if minutes <= 0 or len(apps) < 2:
        return 0.0
    switches = sum(1 for a, b in zip(apps, apps[1:], strict=False) if a != b)
    return round(switches / (minutes / 60), 1)


def longest_gap(times: list[datetime]) -> timedelta:
    """The longest stretch with no activity at all — a break, if it is long enough."""
    if len(times) < 2:
        return timedelta(0)
    return max((b - a for a, b in zip(times, times[1:], strict=False)), default=timedelta(0))


async def peak_report(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    tz: str = "UTC",
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    zone = ZoneInfo(tz)
    since = moment - timedelta(days=days)

    rows = (
        await session.scalars(
            select(ActivitySample)
            .where(ActivitySample.user_id == user_id, ActivitySample.at >= since)
            .order_by(ActivitySample.at)
        )
    ).all()
    if len(rows) < 20:
        return {"enough_data": False}

    by_day: dict[Any, list[ActivitySample]] = defaultdict(list)
    for r in rows:
        by_day[r.at.astimezone(zone).date()].append(r)

    today = moment.astimezone(zone).date()
    todays = by_day.get(today, [])

    # Context-switch tax over the most recent day that has real data.
    recent = todays or by_day[max(by_day)]
    span = (recent[-1].at - recent[0].at).total_seconds() / 60 if len(recent) > 1 else 0
    tax = switches_per_hour([r.app for r in recent], span)

    # 3M breaks: a break is a gap between samples, so it only counts when the machine was
    # genuinely left alone.
    micro = longest_gap([r.at for r in todays]) if todays else timedelta(0)
    week = [r.at for r in rows if r.at >= moment - timedelta(days=7)]
    meso = longest_gap(week)
    macro = longest_gap([r.at for r in rows])

    from jarvis.services.rhythm import rhythm as rhythm_curve

    curve = await rhythm_curve(session, user_id, tz=tz, now=moment)
    window = curve.get("best_window") if curve.get("enough_data") else None

    return {
        "enough_data": True,
        "switches_per_hour": tax,
        "switch_note": (
            "Every switch costs time and accuracy — the brain does not run two tasks at once."
            if tax >= 12 else "Your attention is holding on one thing at a time."
        ),
        "breaks": {
            "micro_due": micro < MICRO_BREAK,
            "meso_due": meso < MESO_BREAK,
            "macro_due": macro < MACRO_BREAK,
            "longest_today_minutes": round(micro.total_seconds() / 60),
        },
        "peak_window": window,
        "peak_note": (
            f"Put demanding work between {window['start']}:00 and {window['end']}:00."
            if window else "Not enough focus history yet to place your peak."
        ),
    }


if __name__ == "__main__":  # self-check of the two pure pieces
    assert switches_per_hour(["a", "a", "b", "b", "a"], 60) == 2.0
    assert switches_per_hour(["a", "a", "a"], 60) == 0.0
    assert switches_per_hour([], 60) == 0.0
    base = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    gaps = [base, base + timedelta(minutes=1), base + timedelta(minutes=45)]
    assert longest_gap(gaps) == timedelta(minutes=44)
    assert longest_gap([base]) == timedelta(0)
    print("ok")
