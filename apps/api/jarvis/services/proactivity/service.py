"""Habit streaks and a meeting-prep card, both read off data we already keep.

Streaks come from focus WorkSessions and completed Tasks; meeting prep pulls the next
calendar event plus the mail and deadlines around it. No new writes, no model calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.db.models.domain import Commitment, Task, WorkSession
from jarvis.db.models.source import SourceObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def coming_up(
    session: AsyncSession, user_id: uuid.UUID, *, hours: int = 48
) -> list[dict[str, Any]]:
    """About-to-forget (second-brain #24): the deadlines and promises about to come due,
    surfaced just before you need them, newest-need first."""
    now = datetime.now(UTC)
    horizon = now + timedelta(hours=hours)
    out: list[dict[str, Any]] = []

    tasks = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user_id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at.is_not(None),
                Task.due_at <= horizon,
            )
        )
    ).all()
    for t in tasks:
        out.append({"type": "deadline", "text": t.title, "when": t.due_at.isoformat(),
                    "id": str(t.id), "overdue": t.due_at < now})

    commitments = (
        await session.scalars(
            select(Commitment).where(
                Commitment.user_id == user_id,
                Commitment.status == "open",
                Commitment.due_at.is_not(None),
                Commitment.due_at <= horizon,
            )
        )
    ).all()
    for c in commitments:
        out.append({"type": "commitment", "text": c.text, "when": c.due_at.isoformat(),
                    "id": str(c.id), "overdue": c.due_at < now})

    out.sort(key=lambda r: r["when"])
    return out


def streak_of(days: set[date], *, today: date) -> dict[str, int]:
    """Current and longest run of consecutive days in ``days``. The current streak counts
    only if it reaches today or yesterday (a gap of one day is grace, two breaks it)."""
    if not days:
        return {"current": 0, "longest": 0}
    ordered = sorted(days)
    longest = run = 1
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    # Walk back from today while the day is present.
    current = 0
    anchor = today if today in days else today - timedelta(days=1)
    if anchor in days:
        d = anchor
        while d in days:
            current += 1
            d -= timedelta(days=1)
    return {"current": current, "longest": longest}


async def habit_streaks(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC"
) -> dict[str, Any]:
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()

    focus_rows = (
        await session.scalars(
            select(WorkSession.started_at).where(
                WorkSession.user_id == user_id, WorkSession.source == "focus"
            )
        )
    ).all()
    focus_days = {dt.astimezone(zone).date() for dt in focus_rows if dt}

    done_rows = (
        await session.scalars(
            select(Task.completed_at).where(
                Task.user_id == user_id, Task.status == "done", Task.completed_at.is_not(None)
            )
        )
    ).all()
    done_days = {dt.astimezone(zone).date() for dt in done_rows if dt}

    return {
        "focus": streak_of(focus_days, today=today),
        "done": streak_of(done_days, today=today),
    }


def _keywords(title: str) -> list[str]:
    stop = {"the", "and", "for", "with", "meeting", "call", "sync", "review", "1:1", "a", "of"}
    return [w for w in title.lower().replace("/", " ").split() if len(w) > 3 and w not in stop][:5]


async def meeting_prep(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC"
) -> dict[str, Any] | None:
    """The next upcoming calendar event, with the mail from its organizer and the
    deadlines that sit near it — the context you'd want before walking in."""
    now = datetime.now(UTC)
    event = await session.scalar(
        select(SourceObject)
        .where(
            SourceObject.user_id == user_id,
            SourceObject.kind == "calendar_event",
            SourceObject.occurred_at >= now,
        )
        .order_by(SourceObject.occurred_at.asc())
        .limit(1)
    )
    if event is None:
        return None

    organizer = event.author
    keywords = _keywords(event.title or "")
    mail_q = select(SourceObject).where(
        SourceObject.user_id == user_id,
        SourceObject.kind == "email",
        SourceObject.occurred_at >= now - timedelta(days=21),
    )
    if organizer:
        mail_q = mail_q.where(SourceObject.author.ilike(f"%{organizer}%"))
    related_mail = (
        await session.scalars(mail_q.order_by(SourceObject.occurred_at.desc()).limit(5))
    ).all()

    when = event.occurred_at
    nearby = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user_id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at.is_not(None),
                Task.due_at >= when - timedelta(days=1),
                Task.due_at <= when + timedelta(days=1),
            )
        )
    ).all()

    return {
        "title": event.title,
        "when": when.isoformat() if when else None,
        "organizer": organizer,
        "url": event.url,
        "keywords": keywords,
        "related_mail": [
            {"from": m.author, "subject": m.title,
             "when": m.occurred_at.isoformat() if m.occurred_at else None}
            for m in related_mail
        ],
        "nearby_deadlines": [
            {"title": t.title, "due": t.due_at.isoformat() if t.due_at else None}
            for t in nearby
        ],
    }
