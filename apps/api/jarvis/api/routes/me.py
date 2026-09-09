"""Your data: export, audit trail, a focus view, and account wipe (FEATURES-50 44/45/9/8)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import delete, func, select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.domain import Goal, Task
from jarvis.db.models.ops import AuditLog, Memory, Profile, Routine

router = APIRouter(prefix="/v1", tags=["me"])


@router.get("/export")
async def export(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Everything the account holds, as one JSON — yours to keep or move."""

    async def rows(model, **where):  # noqa: ANN001, ANN202
        stmt = select(model).where(model.user_id == user.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return (await session.scalars(stmt)).all()

    def dump(obj) -> dict[str, Any]:  # noqa: ANN001
        out: dict[str, Any] = {}
        for col in obj.__table__.columns:
            val = getattr(obj, col.name)
            if col.name in ("credentials", "public_key_pem", "payload_hash"):
                continue  # never export secrets or key material
            out[col.name] = val.isoformat() if isinstance(val, datetime) else val
        return out

    profile = await session.get(Profile, user.id)
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "profile": dump(profile) if profile else None,
        "goals": [dump(g) for g in await rows(Goal)],
        "tasks": [dump(t) for t in await rows(Task)],
        "routines": [dump(r) for r in await rows(Routine)],
        "memories": [
            {k: v for k, v in dump(m).items() if k != "embedding"} for m in await rows(Memory)
        ],
        "conversations": [dump(c) for c in await rows(Conversation)],
        "messages": [dump(m) for m in await rows(ChatMessage)],
    }


@router.get("/audit")
async def audit(
    user: CurrentUser, session: SessionDep, action: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """The account's own audit trail — who did what, when — filterable by action."""
    stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == user.id)
        .order_by(AuditLog.id.desc())
        .limit(min(limit, 300))
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await session.scalars(stmt)).all()
    return [
        {
            "id": str(r.id),
            "at": r.created_at.isoformat(),
            "actor": r.actor,
            "action": r.action,
            "subject_type": r.subject_type,
            "subject_id": r.subject_id,
            "detail": r.detail or {},
            "correlation_id": r.correlation_id,
        }
        for r in rows
    ]


@router.get("/audit/actions")
async def audit_actions(user: CurrentUser, session: SessionDep) -> list[str]:
    rows = (
        await session.scalars(
            select(AuditLog.action).where(AuditLog.user_id == user.id).distinct()
        )
    ).all()
    return sorted(rows)


@router.get("/self-model")
async def self_model(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """A portable, versioned bundle of everything JARVIS has learned to model you (#50) —
    style, words, rhythm, relationships, promises and graded decisions. No secrets."""
    from jarvis.services.self_model import build_self_model

    tz = user.timezone or get_settings().timezone
    return await build_self_model(session, user.id, tz=tz)


@router.get("/mood")
async def mood(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """A gentle private sentiment line from your own words (#18) — on your account only."""
    from jarvis.services.mood import mood_trend

    return await mood_trend(session, user.id)


@router.get("/focus-analytics")
async def focus_analytics_route(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Deep-work time, the apps that pull you away, and your best focus window (#36)."""
    from jarvis.services.focus_analytics import focus_analytics

    tz = user.timezone or get_settings().timezone
    return await focus_analytics(session, user.id, tz=tz)


@router.get("/timetravel")
async def timetravel(
    user: CurrentUser, session: SessionDep, day: date
) -> dict[str, Any]:
    """Reconstruct a past day from everything captured that day (#30). day = YYYY-MM-DD."""
    from jarvis.services.timetravel import reconstruct_day

    tz = user.timezone or get_settings().timezone
    return await reconstruct_day(session, user.id, day, tz=tz)


@router.get("/health-correlation")
async def health_correlation(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """What moves your day: correlate sleep/steps with productivity (#38)."""
    from jarvis.services.health_metrics import correlation

    tz = user.timezone or get_settings().timezone
    return await correlation(session, user.id, tz=tz)


@router.get("/peak")
async def peak(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Peak-performance coach: context-switch tax, the 3M breaks, and your biological peak."""
    from jarvis.services.peak import peak_report

    tz = user.timezone or get_settings().timezone
    return await peak_report(session, user.id, tz=tz)


@router.get("/rhythm")
async def rhythm(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Your energy curve by hour — when you focus and when you slump (#15), so nudges land
    when you're receptive."""
    from jarvis.services.rhythm import rhythm as compute_rhythm

    tz = user.timezone or get_settings().timezone
    return await compute_rhythm(session, user.id, tz=tz)


@router.get("/focus")
async def focus(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The one thing to do now, plus what has slipped (FEATURES-50 8/9)."""
    now = datetime.now(UTC)
    zone = ZoneInfo(user.timezone or get_settings().timezone)

    open_dated = (
        await session.scalars(
            select(Task)
            .where(
                Task.user_id == user.id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at.is_not(None),
            )
            .order_by(Task.due_at)
        )
    ).all()
    overdue = [t for t in open_dated if t.due_at < now]
    upcoming = [t for t in open_dated if t.due_at >= now]

    def line(t: Task) -> dict[str, Any]:
        due_local = t.due_at.astimezone(zone)
        return {
            "id": str(t.id),
            "title": t.title,
            "due_at": t.due_at.isoformat(),
            "due_local": due_local.strftime("%a %d %b, %H:%M"),
            "minutes_away": round((t.due_at - now).total_seconds() / 60),
        }

    # The focus is the most overdue thing, else the soonest thing due.
    pick = overdue[0] if overdue else (upcoming[0] if upcoming else None)
    return {
        "now": now.astimezone(zone).strftime("%A %d %B, %H:%M"),
        "focus": line(pick) if pick else None,
        "focus_reason": ("overdue" if overdue else "next up") if pick else None,
        "overdue": [line(t) for t in overdue[:10]],
        "upcoming": [line(t) for t in upcoming[:10]],
        "counts": {"overdue": len(overdue), "upcoming": len(upcoming)},
    }


@router.post("/account/wipe")
async def wipe(user: CurrentUser, session: SessionDep, confirm: str = "") -> dict[str, Any]:
    """Delete the account's content. ``confirm=DELETE`` required. Keeps the audit log,
    which is append-only evidence, and the user row itself; everything else goes."""
    from jarvis.core.errors import Conflict

    if confirm != "DELETE":
        raise Conflict("Pass confirm=DELETE to wipe this account's data")
    counts: dict[str, int] = {}
    for model in (ChatMessage, Conversation, Task, Goal, Routine, Memory):
        result = await session.execute(delete(model).where(model.user_id == user.id))
        counts[model.__tablename__] = int(result.rowcount or 0)
    profile = await session.get(Profile, user.id)
    if profile is not None:
        await session.delete(profile)
    session.add(
        AuditLog(
            user_id=user.id, actor="user", action="account.wiped",
            subject_type="user", subject_id=str(user.id), detail=counts,
        )
    )
    await session.flush()
    return {"wiped": counts}


@router.get("/review/weekly")
async def weekly_review(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The past 7 days at a glance (FEATURES-50 34): done, slipped, focus minutes,
    where the time went, and what is due next week."""
    from jarvis.db.models.domain import WorkSession
    from jarvis.services import activity

    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    zone = ZoneInfo(user.timezone or get_settings().timezone)

    done = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user.id,
                Task.completed_at >= week_ago,
                Task.status == "done",
            )
        )
    ).all()
    slipped = (
        await session.scalars(
            select(Task).where(
                Task.user_id == user.id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at.is_not(None),
                Task.due_at < now,
                Task.due_at >= week_ago,
            )
        )
    ).all()
    upcoming = (
        await session.scalars(
            select(Task)
            .where(
                Task.user_id == user.id,
                Task.status.in_(("open", "in_progress")),
                Task.due_at >= now,
                Task.due_at < now + timedelta(days=7),
            )
            .order_by(Task.due_at)
        )
    ).all()
    focus_minutes = int(
        await session.scalar(
            select(func.coalesce(func.sum(WorkSession.active_minutes), 0)).where(
                WorkSession.user_id == user.id, WorkSession.started_at >= week_ago
            )
        )
        or 0
    )
    screen = await activity.summary(session, user.id, week_ago, now)

    return {
        "from": week_ago.astimezone(zone).strftime("%a %d %b"),
        "to": now.astimezone(zone).strftime("%a %d %b"),
        "done": len(done),
        "done_titles": [t.title for t in done[:10]],
        "slipped": len(slipped),
        "slipped_titles": [t.title for t in slipped[:10]],
        "focus_hours": round(focus_minutes / 60, 1),
        "top_apps": screen[:6],
        "upcoming": [
            {"title": t.title, "due": t.due_at.astimezone(zone).strftime("%a %d %b, %H:%M")}
            for t in upcoming[:10]
        ],
    }
