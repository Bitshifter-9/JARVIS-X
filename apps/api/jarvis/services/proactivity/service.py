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


async def habit_coach(
    session: AsyncSession, user_id: uuid.UUID, *, tz: str = "UTC"
) -> dict[str, Any]:
    """Habit coach (#31): beyond the streak number — celebrate a roll, and offer the
    smallest next step when one has slipped. Kind, never naggy. Reads the streaks already
    computed; no model."""
    streaks = await habit_streaks(session, user_id, tz=tz)
    labels = {"focus": ("focus session", "focused"), "done": ("task", "shipped a task")}

    def line(kind: str, s: dict[str, int]) -> dict[str, Any]:
        cur, longest = s["current"], s["longest"]
        noun, verb = labels[kind]
        if cur >= 3:
            return {"tone": "roll", "message": f"{cur} days {verb} — you're on a roll. Keep it."}
        if cur == 0 and longest >= 3:
            return {"tone": "slip",
                    "message": f"You had a {longest}-day streak. One {noun} today restarts it."}
        if cur >= 1:
            return {"tone": "steady", "message": f"Day {cur}. Two more makes it a streak."}
        return {"tone": "idle", "message": None}

    return {kind: {**streaks[kind], **line(kind, streaks[kind])} for kind in ("focus", "done")}


async def what_mattered(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 5
) -> list[dict[str, Any]]:
    """"What mattered" digest (#25): the few things that actually need you now, drawn from
    across the signals and ranked — a one-glance summary for the home screen, not another
    list. Composes deadlines/promises due, replies owed, and who you've gone quiet on. No
    model, no new writes."""
    from jarvis.services.relationships import relationships
    from jarvis.services.triage import sender_name

    items: list[dict[str, Any]] = []
    for it in await coming_up(session, user_id, hours=24):
        items.append({
            "kind": it["type"], "text": it["text"],
            "reason": "overdue" if it["overdue"] else "due soon",
            "route": "goals", "score": 100 if it["overdue"] else 80,
        })
    for it in await owed_replies(session, user_id, limit=3):
        items.append({
            "kind": "reply", "text": sender_name(it["sender"]),
            "reason": "waiting on your reply", "route": "insights", "score": 60,
        })
    quiet = [r for r in await relationships(session, user_id, limit=10) if r["quiet"]]
    if quiet:
        q = quiet[0]
        items.append({
            "kind": "reconnect", "text": q["name"],
            "reason": f"quiet {q['days_since']}d", "route": "insights", "score": 40,
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


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


async def owed_replies(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    hours_min: int = 3,
    days_back: int = 7,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Dropped-thread finder (second-brain #28): people who asked you something recently
    that you may not have answered. Reads the triage ``needs_reply`` classifications — one
    row per sender (their most recent), older than a few hours so an in-flight reply isn't
    flagged — and skips senders you've muted. No model, no new writes."""
    from jarvis.db.models.domain import ReminderMute
    from jarvis.db.models.ops import AuditLog
    from jarvis.services.reminders import signature_for

    now = datetime.now(UTC)
    # Scan the recent needs_reply classifications; the "how long ago" that matters is the
    # message's own time, applied below, not when triage happened to log it.
    logs = (
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "triage.classified",
                AuditLog.detail["category"].astext == "needs_reply",
                AuditLog.created_at >= now - timedelta(days=days_back + 1),
            )
            .order_by(AuditLog.created_at.desc())
        )
    ).all()
    if not logs:
        return []
    oldest = now - timedelta(days=days_back)
    newest = now - timedelta(hours=hours_min)

    muted = set(
        (
            await session.scalars(
                select(ReminderMute.signature).where(ReminderMute.user_id == user_id)
            )
        ).all()
    )

    ids: list[uuid.UUID] = []
    for lg in logs:
        try:
            ids.append(uuid.UUID(lg.subject_id or ""))
        except (ValueError, TypeError):
            continue
    objs = {
        o.id: o
        for o in (
            await session.scalars(select(SourceObject).where(SourceObject.id.in_(ids)))
        ).all()
    } if ids else {}

    # Gather qualifying messages, then keep the newest one per sender.
    candidates: list[tuple[datetime, Any]] = []
    for lg in logs:
        try:
            obj = objs.get(uuid.UUID(lg.subject_id or ""))
        except (ValueError, TypeError):
            continue
        if obj is None:
            continue
        sig = signature_for(obj.provider, obj.author, obj.title)
        if sig and sig in muted:
            continue
        when = obj.occurred_at or lg.created_at
        if when > newest or when < oldest:  # too recent (reply may be in flight) or too old
            continue
        candidates.append((when, obj))

    candidates.sort(key=lambda c: c[0], reverse=True)  # newest message first
    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()
    for when, obj in candidates:
        key = (obj.provider, (obj.author or "someone").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "sender": obj.author or "someone",
            "subject": obj.title,
            "provider": obj.provider,
            "when": when.isoformat(),
            "url": obj.url,
            "source_id": str(obj.id),
        })
        if len(out) >= limit:
            break
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
