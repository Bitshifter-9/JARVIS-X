"""Your data: export, audit trail, a focus view, and account wipe (FEATURES-50 44/45/9/8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import delete, select

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
