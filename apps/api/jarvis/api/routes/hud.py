"""The HUD: one call for the home screen, and a live stream behind it (Phase 10.3.1).

``/v1/hud`` is every tile in one round trip. ``/v1/live`` is Server-Sent Events: each
action, run or noteworthy audit row that lands after the stream opened is pushed within
a second, so the orb reacts to what the workers are doing without a refresh.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.api.routes.timeline import _NOTEWORTHY, timeline
from jarvis.db.models.agent import Action, AgentRun, Approval, Evidence
from jarvis.db.models.domain import Goal, GoalPrediction, Task
from jarvis.db.models.ops import AuditLog, Memory, Routine, WorkerHeartbeat
from jarvis.db.models.source import SourceAccount
from jarvis.db.session import get_sessionmaker
from jarvis.services.device import DeviceService

router = APIRouter(prefix="/v1", tags=["hud"])

EXPECTED_WORKERS = ("agent", "connector", "notify", "heartbeat", "scheduler")
POLL_SECONDS = 1.0
PING_SECONDS = 15.0


@router.get("/hud")
async def hud(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    zone = ZoneInfo(user.timezone or "Asia/Kolkata")
    local = now.astimezone(zone)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    day_end = day_start + timedelta(days=1)

    async def count(stmt) -> int:  # noqa: ANN001
        return int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    open_tasks = select(Task.id).where(Task.user_id == user.id, Task.status == "open")
    due_today = await count(open_tasks.where(Task.due_at >= day_start, Task.due_at < day_end))
    overdue = await count(open_tasks.where(Task.due_at < now))

    latest = (
        select(GoalPrediction.goal_id, GoalPrediction.severity)
        .where(GoalPrediction.user_id == user.id)
        .distinct(GoalPrediction.goal_id)
        .order_by(GoalPrediction.goal_id, GoalPrediction.created_at.desc())
        .subquery()
    )
    at_risk = int(
        await session.scalar(
            select(func.count())
            .select_from(latest)
            .join(Goal, Goal.id == latest.c.goal_id)
            .where(Goal.status == "active", latest.c.severity.in_(("at_risk", "critical")))
        )
        or 0
    )
    verified_today = await count(
        select(Evidence.id).where(
            Evidence.user_id == user.id,
            Evidence.verdict == "verified",
            Evidence.created_at >= day_start,
        )
    )
    actions_today = await count(
        select(Action.id).where(Action.user_id == user.id, Action.created_at >= day_start)
    )
    pending = await count(
        select(Approval.id).where(
            Approval.user_id == user.id, Approval.decision.is_(None), Approval.expires_at > now
        )
    )
    memories = await count(select(Memory.id).where(Memory.user_id == user.id))

    workers = {
        name: (
            None
            if (p := pulses.get(name)) is None
            else round((now - p.last_tick_at).total_seconds())
        )
        for pulses in [{p.name: p for p in (await session.scalars(select(WorkerHeartbeat))).all()}]
        for name in EXPECTED_WORKERS
    }

    devices = DeviceService(session)
    device_rows = [
        {
            "id": str(d.id),
            "name": d.name,
            "platform": d.platform,
            "online": await devices.is_online(d.id),
        }
        for d in await devices.list_devices(user.id)
        if d.is_active
    ]

    scans = [
        {
            "provider": a.provider,
            "account": a.display_name or a.external_id,
            "at": (a.last_sync_result or {}).get("at") or (
                a.last_synced_at.isoformat() if a.last_synced_at else None
            ),
            "new": (a.last_sync_result or {}).get("new"),
            "error": a.last_error,
        }
        for a in (
            await session.scalars(
                select(SourceAccount).where(
                    SourceAccount.user_id == user.id, SourceAccount.revoked_at.is_(None)
                )
            )
        ).all()
    ]

    routines = [
        {
            "id": str(r.id),
            "name": r.name,
            "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
            "channel": r.channel,
        }
        for r in (
            await session.scalars(
                select(Routine)
                .where(Routine.user_id == user.id, Routine.enabled.is_(True))
                .order_by(Routine.next_run_at.nulls_last())
                .limit(3)
            )
        ).all()
    ]

    return {
        "at": now.isoformat(),
        "greeting": _greeting(local, user.display_name),
        "tiles": {
            "due_today": due_today,
            "overdue": overdue,
            "at_risk": at_risk,
            "verified_today": verified_today,
            "actions_today": actions_today,
            "pending_approvals": pending,
            "memories": memories,
        },
        "workers": workers,
        "devices": device_rows,
        "scans": scans,
        "routines": routines,
        "recent": await timeline(user, session, limit=12),
    }


def _greeting(local: datetime, name: str | None) -> str:
    hour = local.hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    first = (name or "").split(" ")[0]
    return f"Good {part}{', ' + first if first else ''}."


@router.get("/live")
async def live(
    request: Request, user: CurrentUser, seconds: int | None = None
) -> StreamingResponse:
    """Server-Sent Events of what the system does, from now on. ``seconds`` closes the
    stream after that long (clients reconnect; tests need an end).

    ponytail: polls the three tables every second on a dedicated session instead of
    LISTEN/NOTIFY — one user, three indexed queries; switch to NOTIFY if it ever matters.
    """
    user_id = user.id

    async def stream():  # noqa: ANN202
        since = datetime.now(UTC)
        last_ping = since
        deadline = None if seconds is None else since + timedelta(seconds=seconds)
        yield _frame({"kind": "hello", "at": since.isoformat()})
        while not await request.is_disconnected():
            if deadline is not None and datetime.now(UTC) >= deadline:
                break
            events: list[dict[str, Any]] = []
            async with get_sessionmaker()() as session:
                for a in (
                    await session.scalars(
                        select(Action).where(Action.user_id == user_id, Action.updated_at > since)
                    )
                ).all():
                    events.append(
                        {
                            "kind": "action",
                            "title": a.tool,
                            "status": a.status,
                            "risk": a.risk,
                            "at": a.updated_at.isoformat(),
                            "id": str(a.id),
                        }
                    )
                for r in (
                    await session.scalars(
                        select(AgentRun).where(
                            AgentRun.user_id == user_id, AgentRun.updated_at > since
                        )
                    )
                ).all():
                    events.append(
                        {
                            "kind": "run",
                            "title": f"{r.trigger} run",
                            "status": r.status,
                            "at": r.updated_at.isoformat(),
                            "id": str(r.id),
                        }
                    )
                for e in (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.user_id == user_id,
                            AuditLog.created_at > since,
                            AuditLog.action.in_(_NOTEWORTHY | {"routine.finished"}),
                        )
                    )
                ).all():
                    events.append(
                        {
                            "kind": "event",
                            "title": e.action,
                            "detail": e.detail or {},
                            "at": e.created_at.isoformat(),
                            "id": str(e.id),
                        }
                    )
            now = datetime.now(UTC)
            for event in sorted(events, key=lambda x: x["at"]):
                yield _frame(event)
            if events:
                since = max(datetime.fromisoformat(e["at"]) for e in events)
            elif (now - last_ping).total_seconds() >= PING_SECONDS:
                yield _frame({"kind": "ping", "at": now.isoformat()})
                last_ping = now
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
