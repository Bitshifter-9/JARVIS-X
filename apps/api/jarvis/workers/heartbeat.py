"""The heartbeat: proactive without being a model that wakes up and improvises.

The pattern is borrowed from OpenClaw's heartbeat daemon — a periodic check that runs
whether or not anyone has spoken to the assistant. The difference is what the check *is*.
OpenClaw hands a cheap model a HEARTBEAT.md and lets it decide; here the check is
arithmetic the goal engine already does (blueprint §6), and a model is never consulted.
A heartbeat that hallucinates urgency is worse than none.

Every ``heartbeat_minutes`` it computes the brief for each user and, if a goal is newly
at risk today, sends **one** alert for it through the escalation policy — which still
applies quiet hours and the per-day cap.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.core.overrides import apply_overrides
from jarvis.db.models.identity import User
from jarvis.db.models.ops import AuditLog, NotificationEndpoint
from jarvis.db.session import session_scope
from jarvis.services.modules import ModuleService
from jarvis.services.notification import NotificationService
from jarvis.workers.pulse import pulse

log = get_logger(__name__)


async def _already_told_today(session: AsyncSession, user_id: uuid.UUID, goal_id: str) -> bool:
    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.action == "heartbeat.alerted",
            AuditLog.subject_id == goal_id,
            AuditLog.created_at >= func.date_trunc("day", func.now()),
        )
    )
    return bool(count)


async def beat(
    session: AsyncSession, *, senders: dict | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """One heartbeat over every user who can be reached. Returns what it did."""
    moment = now or datetime.now(UTC)
    reachable = (
        await session.scalars(
            select(User)
            .join(NotificationEndpoint, NotificationEndpoint.user_id == User.id)
            .where(NotificationEndpoint.enabled.is_(True))
            .distinct()
        )
    ).all()

    if senders is None:
        from jarvis.workers.notify import build_senders

        senders = build_senders(session)

    from jarvis.services import activity

    await activity.prune(session)
    alerted: list[str] = []
    nudged: list[str] = []
    learned: list[str] = []
    repaired: list[str] = []
    for user in reachable:
        try:
            from jarvis.services.repair import repair

            if await repair(session, user.id, now=moment):
                repaired.append(str(user.id))
        except Exception as exc:  # noqa: BLE001
            log.warning("repair_failed", error=str(exc)[:200])
        # The focus guard and the learning loop ride the same clock (PLAN.md 10.6.4–5).
        try:
            if (await activity.focus_guard(session, user.id, now=moment)).get("nudged"):
                nudged.append(str(user.id))
        except Exception as exc:  # noqa: BLE001
            log.warning("focus_guard_failed", error=str(exc)[:200])
        try:
            if (await activity.learn(session, user.id, now=moment)).get("added"):
                learned.append(str(user.id))
        except Exception as exc:  # noqa: BLE001
            log.warning("learning_failed", error=str(exc)[:200])
        brief = await ModuleService(session).morning_brief(user.id, now=moment)
        for risk in brief.at_risk:
            goal_id = str(risk.goal_id)
            if await _already_told_today(session, user.id, goal_id):
                continue
            result = await NotificationService(session, senders=senders).notify(
                user.id,
                title=f"{risk.title} is {risk.severity.replace('_', ' ')}",
                body=f"Completion probability {risk.probability:.0%}. {brief.headline}",
                attempt=0,
                now=moment,
            )
            if result.delivered:
                session.add(
                    AuditLog(
                        user_id=user.id,
                        actor="system",
                        action="heartbeat.alerted",
                        subject_type="goal",
                        subject_id=goal_id,
                        detail={"probability": risk.probability, "severity": risk.severity},
                    )
                )
                alerted.append(goal_id)
    await session.flush()
    return {
        "users": len(reachable),
        "alerted": alerted,
        "nudged": nudged,
        "learned": learned,
        "repaired": repaired,
    }


async def run_forever(*, minutes: float | None = None) -> None:
    interval = (minutes or get_settings().heartbeat_minutes) * 60
    log.info("heartbeat_started", every_minutes=interval / 60)
    while True:
        try:
            async with session_scope() as session:
                await apply_overrides(session)
                await pulse(session, "heartbeat")
                outcome = await beat(session)
            if outcome["alerted"]:
                log.info("heartbeat_alerted", goals=outcome["alerted"])
        except Exception as exc:  # noqa: BLE001
            log.error("heartbeat_failed", error=str(exc)[:300])
        await asyncio.sleep(interval)


if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
