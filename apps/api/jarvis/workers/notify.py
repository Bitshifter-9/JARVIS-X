"""The notification worker: drains ``schedule.escalate`` and climbs the ladder.

The scheduler fires a rung into the queue; this is what turns that row into a message a
human actually receives. Keeping it a worker rather than doing it inside the scheduler
tick is what makes an unreachable provider a retryable job instead of a missed deadline.

**The attempt number is derived, never carried.** It is the count of alerts already sent
for this task, read from ``audit_log`` — so a redelivered job cannot restart the ladder
at rung 0 and shout through every channel again.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.ops import AuditLog
from jarvis.db.queue import JobQueue
from jarvis.services.notification import Channel, NotificationService

log = get_logger(__name__)
KINDS = ["schedule.escalate", "approval.escalate"]


class TelegramSender:
    """Adapts ``TelegramService`` to the sender contract, with the inline buttons kept.

    The buttons are the point: an alert the user can acknowledge from the notification
    is what stops the ladder, and a plain text message cannot be acknowledged.
    """

    def __init__(self, session: AsyncSession, transport) -> None:  # noqa: ANN001
        self.session = session
        self.transport = transport

    async def send(self, address: str, *, title: str, body: str, task_id=None, data=None) -> None:  # noqa: ANN001, ARG002
        from jarvis.connectors.telegram.service import TelegramService
        from jarvis.db.models.domain import Task

        service = TelegramService(self.session, self.transport)
        task = await self.session.get(Task, task_id) if task_id else None
        if task is not None and task.due_at is not None:
            await service.send_deadline_alert(
                address,
                task_title=task.title,
                due_at=task.due_at,
                kind="deadline",
                task_id=task_id,
            )
            return
        await service.transport.call(
            "sendMessage", {"chat_id": address, "text": f"{title}\n{body}"}
        )


def build_senders(session: AsyncSession) -> dict[Channel, Any]:
    """Wire the channels this deployment is actually configured for.

    An unconfigured channel is simply absent, and ``NotificationService`` treats a missing
    sender as "not delivered" — so the ladder falls through to the next rung rather than
    crashing on a credential that was never set.
    """
    s = get_settings()
    senders: dict[Channel, Any] = {}

    if s.fcm_credentials_path:
        from jarvis.connectors.fcm import FcmSender

        senders[Channel.PUSH] = FcmSender(s.fcm_credentials_path, s.fcm_project_id)

    if s.telegram_bot_token:
        from jarvis.connectors.telegram.client import TelegramClient

        senders[Channel.TELEGRAM] = TelegramSender(session, TelegramClient(s.telegram_bot_token))

    if s.whatsapp_phone_number_id and s.whatsapp_access_token:
        from jarvis.connectors.whatsapp import WhatsAppSender

        senders[Channel.WHATSAPP] = WhatsAppSender(
            s.whatsapp_phone_number_id, s.whatsapp_access_token
        )

    if s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number:
        from jarvis.connectors.twilio import TwilioCaller

        senders[Channel.CALL] = TwilioCaller(
            s.twilio_account_sid, s.twilio_auth_token, s.twilio_from_number
        )

    return senders


async def attempts_so_far(session: AsyncSession, task_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "notification.sent",
                AuditLog.subject_type == "task",
                AuditLog.subject_id == str(task_id),
            )
        )
        or 0
    )


async def handle_escalate(session: AsyncSession, job) -> dict[str, Any]:  # noqa: ANN001
    payload = job.payload or {}
    raw_task_id = payload.get("task_id")
    if not raw_task_id:
        return {"skipped": "no task"}

    task_id = uuid.UUID(raw_task_id)
    service = NotificationService(session, senders=build_senders(session))
    result = await service.escalate_task(
        job.user_id, task_id, attempt=await attempts_so_far(session, task_id)
    )
    return {
        "decision": result.plan.decision.value,
        "channel": result.plan.channel.value if result.plan.channel else None,
        "delivered": result.delivered,
        "reason": result.plan.reason,
    }


# ── approvals: a push now, a phone call if nobody answers (PLAN.md 10.2.1) ──────────
async def handle_approval(
    session: AsyncSession, job, *, senders: dict[Channel, Any] | None = None
) -> dict[str, Any]:  # noqa: ANN001
    from datetime import UTC, datetime

    from jarvis.api.routes.approvals import _summarize
    from jarvis.connectors.twilio.voice import approval_call_url
    from jarvis.db.models.agent import Action, Approval
    from jarvis.db.models.ops import NotificationEndpoint
    from jarvis.services.notification import NotificationService, in_quiet_hours

    payload = job.payload or {}
    approval = await session.get(Approval, uuid.UUID(payload["approval_id"]))
    if approval is None or approval.decision is not None:
        return {"skipped": "decided or gone"}
    if approval.expires_at <= datetime.now(UTC):
        return {"skipped": "expired"}
    action = await session.get(Action, approval.action_id)
    summary = _summarize(action.tool, action.args or {}) if action else "an action"
    senders = build_senders(session) if senders is None else senders
    service = NotificationService(session, senders=senders)
    stage = payload.get("stage", "notify")
    s = get_settings()

    if stage == "notify":
        pushed = False
        push = senders.get(Channel.PUSH)
        endpoint = next(
            (e for e in await service._endpoints(job.user_id) if e.channel == Channel.PUSH.value),
            None,
        )
        if push is not None and endpoint is not None:
            await push.send(
                endpoint.address, title="Approval needed", body=summary,
                data={"kind": "approval", "id": str(approval.id)},
            )
            pushed = True
        minutes = s.twilio_call_for_approval_after_minutes
        if minutes > 0:
            await JobQueue(session).enqueue(
                "approval.escalate",
                {"approval_id": str(approval.id), "stage": "call"},
                user_id=job.user_id,
                priority=15,
                delay_seconds=minutes * 60,
                idempotency_key=f"approval-call:{approval.id}",
                max_attempts=2,
            )
        return {"stage": "notify", "pushed": pushed, "call_in_minutes": minutes}

    # stage == "call"
    caller = senders.get(Channel.CALL)
    address = await session.scalar(
        select(NotificationEndpoint.address).where(
            NotificationEndpoint.user_id == job.user_id,
            NotificationEndpoint.channel == Channel.CALL.value,
            NotificationEndpoint.enabled.is_(True),
        )
    )
    if caller is None or not address:
        return {"stage": "call", "skipped": "no caller or call endpoint"}
    prefs = await service.preferences(job.user_id)
    calls_today = await service._sent_today(job.user_id, channel=Channel.CALL.value)
    if calls_today >= prefs.max_calls_per_day:
        return {"stage": "call", "skipped": "daily call cap"}
    if in_quiet_hours(datetime.now(UTC), prefs):
        return {"stage": "call", "skipped": "quiet hours"}
    await caller.call(address, url=approval_call_url(approval.id))
    session.add(
        AuditLog(
            user_id=job.user_id,
            actor="system",
            action="notification.sent",
            subject_type="approval",
            subject_id=str(approval.id),
            detail={"channel": Channel.CALL.value, "attempt": 0, "title": "Approval needed"},
        )
    )
    await session.flush()
    log.info("approval_call_placed", approval_id=str(approval.id))
    return {"stage": "call", "called": True}


HANDLERS = {"schedule.escalate": handle_escalate, "approval.escalate": handle_approval}


async def run_forever(*, poll_seconds: float = 5.0) -> None:
    from jarvis.workers.loop import drain

    worker_id = f"notify-{uuid.uuid4().hex[:8]}"
    log.info("notify_worker_started", worker_id=worker_id, kinds=KINDS)
    while True:
        try:
            worked = await drain(worker_id, KINDS, HANDLERS, limit=5, pulse_name="notify")
        except Exception as exc:  # noqa: BLE001
            log.error("notify_worker_tick_failed", error=str(exc)[:300])
            worked = False
        if not worked:
            await asyncio.sleep(poll_seconds)

if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
