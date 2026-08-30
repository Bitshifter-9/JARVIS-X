"""Sending alerts, and remembering that we did."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.domain import Task
from jarvis.db.models.identity import User
from jarvis.db.models.ops import AuditLog, NotificationEndpoint
from jarvis.services.notification.policy import (
    Channel,
    Decision,
    DeliveryPlan,
    UserPreferences,
    plan_delivery,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    plan: DeliveryPlan
    delivered: bool
    address: str | None = None


class NotificationService:
    def __init__(self, session: AsyncSession, senders: dict[Channel, object] | None = None) -> None:
        self.session = session
        self.senders = senders or {}

    async def preferences(self, user_id: uuid.UUID) -> UserPreferences:
        settings = get_settings()
        user = await self.session.get(User, user_id)
        endpoints = await self._endpoints(user_id)
        return UserPreferences(
            timezone=user.timezone if user else settings.timezone,
            quiet_hours=next(
                (e.quiet_hours for e in endpoints if e.quiet_hours), settings.quiet_hours
            ),
            max_per_day=settings.max_notifications_per_day,
            max_calls_per_day=settings.max_calls_per_day,
            enabled_channels=tuple(Channel(e.channel) for e in endpoints if e.channel in Channel),
        )

    async def _endpoints(self, user_id: uuid.UUID) -> list[NotificationEndpoint]:
        return list(
            (
                await self.session.scalars(
                    select(NotificationEndpoint)
                    .where(
                        NotificationEndpoint.user_id == user_id,
                        NotificationEndpoint.enabled.is_(True),
                    )
                    .order_by(NotificationEndpoint.escalation_rank)
                )
            ).all()
        )

    async def _sent_today(self, user_id: uuid.UUID, *, channel: str | None = None) -> int:
        query = (
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "notification.sent",
                AuditLog.created_at >= func.date_trunc("day", func.now()),
            )
        )
        if channel:
            query = query.where(AuditLog.detail["channel"].astext == channel)
        return await self.session.scalar(query) or 0

    async def notify(
        self,
        user_id: uuid.UUID,
        *,
        title: str,
        body: str,
        task_id: uuid.UUID | None = None,
        attempt: int = 0,
        due_at: datetime | None = None,
        acknowledged: bool = False,
        now: datetime | None = None,
    ) -> DeliveryResult:
        moment = now or datetime.now(UTC)
        prefs = await self.preferences(user_id)

        plan = plan_delivery(
            now=moment,
            prefs=prefs,
            attempt=attempt,
            sent_today=await self._sent_today(user_id),
            calls_today=await self._sent_today(user_id, channel=Channel.CALL.value),
            due_at=due_at,
            acknowledged=acknowledged,
        )

        if plan.decision is not Decision.SEND:
            log.info(
                "notification_not_sent",
                decision=plan.decision.value, reason=plan.reason, attempt=attempt,
            )
            return DeliveryResult(plan=plan, delivered=False)

        endpoint = next(
            (e for e in await self._endpoints(user_id) if e.channel == plan.channel.value), None
        )
        sender = self.senders.get(plan.channel)
        if endpoint is None or sender is None:
            return DeliveryResult(plan=plan, delivered=False)

        await sender.send(endpoint.address, title=title, body=body, task_id=task_id)
        endpoint.last_used_at = moment

        self.session.add(
            AuditLog(
                user_id=user_id,
                actor="system",
                action="notification.sent",
                subject_type="task",
                subject_id=str(task_id) if task_id else None,
                detail={"channel": plan.channel.value, "attempt": attempt, "title": title},
            )
        )
        await self.session.flush()
        log.info("notification_sent", channel=plan.channel.value, attempt=attempt)
        return DeliveryResult(plan=plan, delivered=True, address=endpoint.address)

    async def escalate_task(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, attempt: int, now: datetime | None = None
    ) -> DeliveryResult:
        """One rung of the ladder for a task, stopping if it has been acknowledged."""
        task = await self.session.get(Task, task_id)
        if task is None or task.status in ("done", "cancelled"):
            return DeliveryResult(
                plan=DeliveryPlan(Decision.SUPPRESS, None, "task is closed"), delivered=False
            )

        return await self.notify(
            user_id,
            title=task.title,
            body=f"Due {task.due_at.isoformat()}" if task.due_at else "Due soon",
            task_id=task_id,
            attempt=attempt,
            due_at=task.due_at,
            now=now,
        )
