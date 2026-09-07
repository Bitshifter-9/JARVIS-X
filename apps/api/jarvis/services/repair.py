"""The fix loop (PLAN.md 10.9.1): the heartbeat already notices failure classes; this
applies the one known repair per class and says "found X, did Y" — once, not every
half hour. Anything it cannot fix from inside the process it reports plainly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, Approval
from jarvis.db.models.job import Job
from jarvis.db.models.ops import AuditLog, WorkerHeartbeat
from jarvis.db.models.source import SourceAccount
from jarvis.db.queue import JobQueue
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

TOKEN_REJECTED = ("invalid_grant", "401", "unauthorized", "revoked", "invalid_token")
WORKER_TOLERANCE_MINUTES = {
    "agent": 5,
    "notify": 5,
    "scheduler": 5,
    "connector": 20,
    "heartbeat": 120,
}


async def _already_reported(session: AsyncSession, user_id: uuid.UUID, found: str) -> bool:
    since = datetime.now(UTC) - timedelta(hours=24)
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "repair.applied",
                AuditLog.created_at >= since,
                AuditLog.detail["found"].astext == found,
            )
        )
    )


async def repair(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    moment = now or datetime.now(UTC)
    fixes: list[dict[str, Any]] = []

    # ── dead-lettered jobs: one second chance, marked so it never loops ──
    dead = (
        await session.scalars(
            select(Job).where(
                Job.user_id == user_id,
                Job.status == "dead_lettered",
                Job.dead_lettered_at >= moment - timedelta(hours=24),
            )
        )
    ).all()
    for job in dead:
        if (job.payload or {}).get("_repaired"):
            continue
        await session.execute(
            text("""
                UPDATE jobs
                SET status='pending', attempts=0, visible_at=clock_timestamp(),
                    dead_lettered_at=NULL, payload = payload || '{"_repaired": true}'::jsonb,
                    updated_at=now()
                WHERE id = :id
            """),
            {"id": job.id},
        )
        fixes.append(
            {
                "found": f"{job.kind} job dead-lettered: {(job.last_error or '')[:80]}",
                "did": "re-queued it once",
            }
        )

    # ── a provider that rejected our token: flag it, stop retrying blindly ──
    accounts = (
        await session.scalars(
            select(SourceAccount).where(
                SourceAccount.user_id == user_id,
                SourceAccount.revoked_at.is_(None),
                SourceAccount.last_error.is_not(None),
                SourceAccount.status != "needs_reconnect",
            )
        )
    ).all()
    for account in accounts:
        if any(marker in (account.last_error or "").lower() for marker in TOKEN_REJECTED):
            account.status = "needs_reconnect"
            fixes.append(
                {
                    "found": f"{account.provider} rejected the token for {account.external_id}",
                    "did": "marked it for reconnection — Connections → Connect",
                }
            )

    # ── an approval about to expire unanswered: ring, don't wait for the clock ──
    expiring = (
        await session.execute(
            select(Approval, Action)
            .join(Action, Action.id == Approval.action_id)
            .where(
                Approval.user_id == user_id,
                Approval.decision.is_(None),
                Approval.expires_at > moment,
                Approval.expires_at <= moment + timedelta(minutes=10),
            )
        )
    ).all()
    for approval, action in expiring:
        job = await JobQueue(session).enqueue(
            "approval.escalate",
            {"approval_id": str(approval.id), "stage": "call"},
            user_id=user_id,
            priority=15,
            idempotency_key=f"approval-call:{approval.id}",
            max_attempts=2,
        )
        if job is not None:
            minutes = max(1, round((approval.expires_at - moment).total_seconds() / 60))
            fixes.append(
                {
                    "found": f"approval for {action.tool} expires in {minutes} min unanswered",
                    "did": "asked the phone to ring you",
                }
            )

    # ── a silent worker: nothing to do from here but say so, once a day ──
    pulses = {p.name: p for p in (await session.scalars(select(WorkerHeartbeat))).all()}
    for name, tolerance in WORKER_TOLERANCE_MINUTES.items():
        pulse = pulses.get(name)
        if pulse is None:
            continue
        silent = (moment - pulse.last_tick_at).total_seconds() / 60
        if silent > tolerance:
            fixes.append(
                {
                    "found": f"{name} worker silent for {round(silent)} min",
                    "did": "cannot restart it from here — `make prod-up` restarts the container",
                }
            )

    # ── record and tell, without repeating yesterday's news ──
    fresh: list[dict[str, Any]] = []
    for fix in fixes:
        if await _already_reported(session, user_id, fix["found"]):
            continue
        session.add(
            AuditLog(
                user_id=user_id,
                actor="system",
                action="repair.applied",
                subject_type="user",
                subject_id=str(user_id),
                detail=fix,
            )
        )
        fresh.append(fix)
    await session.flush()
    if fresh:
        log.info("repairs_applied", count=len(fresh))
        await _tell(session, user_id, fresh)
    return fresh


async def _tell(session: AsyncSession, user_id: uuid.UUID, fixes: list[dict[str, Any]]) -> None:
    from jarvis.services.notification import NotificationService
    from jarvis.workers.notify import build_senders

    body = "; ".join(f"found {f['found']} — {f['did']}" for f in fixes)[:600]
    try:
        await NotificationService(session, senders=build_senders(session)).notify(
            user_id, title="Jarvis fixed something" if len(fixes) else "Jarvis", body=body
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("repair_notify_failed", error=str(exc)[:120])
