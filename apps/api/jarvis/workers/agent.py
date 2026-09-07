"""The agent worker: the ``worker`` entrypoint's brain half (PLAN.md §6).

Until this existed, every job the event service enqueued — ``event.normalize``,
``run.resume``, ``goal.repredict``, ``evidence.verify`` — was written and never read. The
spine *event → task → prediction → approval → verified action* ran only inside the test
suite. This is what makes it run in production.

Job kinds and what each does:

* ``event.normalize`` — a provider object became an event. Extract a deadline from it
  (schema-constrained, cached, voted when unsure), write a **sourced** task that cites the
  span it was read from, and arm the schedule ladder. Structured due dates (Classroom,
  Canvas) skip the model entirely.
* ``agent.run`` — a request from a channel (Telegram free text, a heartbeat finding). Runs
  the LangGraph loop with the production planner and executor, and replies on the channel
  it came from — or sends an approval card if the plan needs a human.
* ``run.resume`` — an approval was decided; continue the suspended run.
* ``goal.repredict`` / ``evidence.verify`` — the small ones.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.domain import Task
from jarvis.db.models.identity import User
from jarvis.db.models.source import Event, SourceObject
from jarvis.services.agent.executor import ToolExecutor
from jarvis.services.agent.planner import LLMPlanner
from jarvis.services.agent.runtime import AgentRuntime, describe, postgres_checkpointer
from jarvis.services.agent.state import Observation
from jarvis.services.event import EventService
from jarvis.services.goal import GoalService
from jarvis.services.memory import MemoryService

log = get_logger(__name__)
KINDS = ["event.normalize", "agent.run", "run.resume", "goal.repredict", "evidence.verify"]

# Below this the extractor's own confidence is a coin flip; the task is still created —
# a missed deadline costs more than a spurious one — but flagged for confirmation.
CONFIRM_BELOW = 0.6


# ── event.normalize ────────────────────────────────────────────────────
async def handle_normalize(session: AsyncSession, job, *, router=None) -> dict[str, Any]:  # noqa: ANN001
    event = await session.scalar(select(Event).where(Event.event_id == job.payload["event_id"]))
    if event is None or event.user_id is None:
        return {"skipped": "no event"}

    events = EventService(session)
    payload = event.payload or {}
    source = await session.scalar(
        select(SourceObject).where(
            SourceObject.user_id == event.user_id,
            SourceObject.provider == event.provider,
            SourceObject.object_id == event.object_id,
        )
    )
    # Channels that push (Slack, OpenClaw) carry their text in the payload and have no
    # source row yet. Make one, so the task can cite it like any other.
    if source is None and payload.get("text"):
        source = await events.upsert_source_object(
            user_id=event.user_id,
            provider=event.provider,
            object_id=event.object_id or "",
            kind=str(payload.get("kind") or "message"),
            title=payload.get("title"),
            excerpt=str(payload["text"])[:4000],
            author=payload.get("author"),
            occurred_at=event.occurred_at,
            url=payload.get("url"),
            account_id=event.account_id,
        )
    if source is None:
        await events.mark_processed(event.event_id)
        return {"skipped": "no content"}

    # One task per source object, however many times the provider redelivers it.
    existing = await session.scalar(
        select(Task).where(Task.source_id == source.id, Task.status != "cancelled").limit(1)
    )
    if existing is not None:
        await events.mark_processed(event.event_id)
        return {"duplicate_of": str(existing.id)}

    user = await session.get(User, event.user_id)
    timezone = (user.timezone if user else None) or get_settings().timezone
    goals = GoalService(session)

    from jarvis.llm.router import LLMRouter

    llm = router or LLMRouter(session)
    triage: dict[str, Any] = {}
    if source.provider in ("gmail", "slack"):
        # Classify, draft a reply if one is owed, remember the sender (PLAN.md 10.6.1).
        # A triage failure must not cost the deadline extraction below.
        from jarvis.db.models.source import SourceAccount
        from jarvis.services.triage import TriageService

        try:
            account = (
                await session.get(SourceAccount, event.account_id) if event.account_id else None
            )
            triage = await TriageService(session, llm).triage(event.user_id, source, account)
        except Exception as exc:  # noqa: BLE001
            log.warning("triage_failed", error=str(exc)[:200])
            triage = {"error": str(exc)[:200]}

    # Disliked sender/channel: the user muted these, so no reminder is created (#14).
    from jarvis.services.reminders import is_muted

    if await is_muted(session, event.user_id, source.provider, source.author, source.title):
        await events.mark_processed(event.event_id)
        return {"muted": True, "provider": source.provider, "triage": triage}

    # Structured deadlines need no model. Classroom and Canvas already told us the date.
    structured = payload.get("due_at")
    if structured:
        task = await goals.create_task(
            event.user_id,
            title=source.title or "(untitled)",
            due_at=datetime.fromisoformat(structured),
            timezone=timezone,
            source_id=source.id,
            confidence=1.0,
            evidence_span=f"{event.provider} due date",
        )
        await events.mark_processed(event.event_id)
        return {"task_id": str(task.id), "structured": True, "triage": triage}

    from jarvis.services.extraction import ExtractionService

    outcome = await ExtractionService(session, llm).extract(
        user_id=event.user_id,
        body=source.excerpt or "",
        subject=source.title or "",
        sender=source.author or "",
        received_at=event.occurred_at,
        timezone=timezone,
    )
    await events.mark_processed(event.event_id)
    if outcome.error:
        # The model could not be reached; the job retries with backoff by raising.
        raise RuntimeError(f"extraction failed: {outcome.error}")
    if outcome.resolved is None:
        return {"deadline": False, "cached": outcome.cached, "triage": triage}

    found = outcome.resolved
    task = await goals.create_task(
        event.user_id,
        title=found.title,
        due_at=found.due_at,
        timezone=found.timezone,
        estimate_minutes=found.estimate_minutes,
        source_id=source.id,
        confidence=found.confidence,
        evidence_span=found.evidence_span,
    )
    # The source tier of memory: what the professor actually wrote, citable later.
    await MemoryService(session).remember(
        event.user_id,
        kind="source",
        content=(
            f"{found.title} is due {found.due_at.isoformat()} ({source.author or event.provider})"
        ),
        provenance={
            "source": event.provider,
            "object_id": event.object_id,
            "task_id": str(task.id),
        },
        importance=0.6,
    )
    log.info(
        "sourced_task_created",
        task_id=str(task.id),
        provider=event.provider,
        confidence=found.confidence,
        voted=outcome.voted,
        cached=outcome.cached,
    )
    return {
        "task_id": str(task.id),
        "confidence": found.confidence,
        "needs_confirmation": found.needs_confirmation or found.confidence < CONFIRM_BELOW,
        "triage": triage,
    }


# ── agent.run ──────────────────────────────────────────────────────────
class AgentWorker:
    """Holds the one checkpointer the runs share; the loop constructs a runtime per job."""

    def __init__(self, checkpointer) -> None:  # noqa: ANN001
        self.checkpointer = checkpointer

    def runtime(self, session: AsyncSession) -> AgentRuntime:
        return AgentRuntime(
            session,
            planner=LLMPlanner(session),
            executor=ToolExecutor(session),
            checkpointer=self.checkpointer,
        )

    async def handle_run(self, session: AsyncSession, job) -> dict[str, Any]:  # noqa: ANN001
        payload = job.payload or {}
        user_id = uuid.UUID(payload["user_id"])
        if payload.get("mode") == "brief":
            # A briefing: prose from gathered context, no planner, no tools.
            from jarvis.services.briefing import compose

            text = await compose(session, user_id, str(payload.get("text", "")))
            await _reply(session, user_id, payload.get("reply_to"), text, None)
            return {"mode": "brief", "reply": text[:300]}
        observations = [
            Observation(
                source=str(payload.get("source", "chat")),
                content=str(payload.get("text", ""))[:8000],
                # A message the linked owner typed is trusted; everything else is not.
                trust="trusted" if payload.get("trust") == "trusted" else "untrusted",
            )
        ]
        # A triggered routine carries the message that tripped it — read, never obeyed.
        if payload.get("context"):
            observations.append(
                Observation(source="message", content=str(payload["context"]), trust="untrusted")
            )
        handle = await self.runtime(session).start(
            user_id,
            observations=observations,
            trigger=str(payload.get("trigger", "channel")),
            simulate=bool(payload.get("simulate", False)),
        )
        await session.commit()

        text = describe(handle)
        await _reply(session, user_id, payload.get("reply_to"), text, handle)
        return {"run_id": str(handle.run_id), "status": handle.status, "reply": text[:300]}

    async def handle_resume(self, session: AsyncSession, job) -> dict[str, Any]:  # noqa: ANN001
        from jarvis.db.models.agent import Action, Approval

        payload = job.payload or {}
        run_id, approved = payload.get("run_id"), payload.get("approved")
        if run_id is None and payload.get("event_id"):
            event = await session.scalar(select(Event).where(Event.event_id == payload["event_id"]))
            data = (event.payload if event else None) or {}
            approval = (
                await session.get(Approval, uuid.UUID(data["approval_id"]))
                if data.get("approval_id")
                else None
            )
            action = await session.get(Action, approval.action_id) if approval else None
            run_id = str(action.run_id) if action and action.run_id else None
            approved = (
                data.get("decision") == "approved"
                if approval is None
                else approval.decision == "approved"
            )
            if event is not None:
                await EventService(session).mark_processed(event.event_id)
        if not run_id:
            # A direct action (the app's Mac panel, Alexa) has no run to resume; the
            # approval *is* the go-ahead, so dispatch it here.
            from jarvis.services.agent.executor import dispatch_action

            action_id = payload.get("action_id")
            if not (action_id and approved):
                return {"skipped": "rejected or no action"}
            current = await session.get(Action, uuid.UUID(action_id))
            if current is not None and current.status != "approved":
                # Already dispatched inline (an app tap) or otherwise moved on.
                return {"skipped": f"action is {current.status}"}
            outcome = await dispatch_action(session, uuid.UUID(action_id))
            await session.commit()
            return {"standalone": True, **{k: v for k, v in outcome.items() if k != "observed"}}

        handle = await self.runtime(session).resume(uuid.UUID(run_id), approved=bool(approved))
        await session.commit()
        text = describe(handle)
        await _reply(
            session, uuid.UUID(handle.state["user_id"]), payload.get("reply_to"), text, handle
        )
        return {"run_id": run_id, "status": handle.status}


async def handle_repredict(session: AsyncSession, job) -> dict[str, Any]:  # noqa: ANN001
    payload = job.payload or {}
    goal_id = payload.get("goal_id")
    if goal_id is None and payload.get("event_id"):
        event = await session.scalar(select(Event).where(Event.event_id == payload["event_id"]))
        goal_id = ((event.payload if event else None) or {}).get("goal_id")
    if not (goal_id and job.user_id):
        return {"skipped": "no goal"}
    prediction = await GoalService(session).predict_goal(job.user_id, uuid.UUID(goal_id))
    return {"probability": prediction.probability, "severity": prediction.severity}


async def handle_verify(session: AsyncSession, job) -> dict[str, Any]:  # noqa: ANN001
    from jarvis.db.models.agent import Action
    from jarvis.services.evidence import EvidenceService

    payload = job.payload or {}
    data = payload
    if payload.get("event_id"):
        event = await session.scalar(select(Event).where(Event.event_id == payload["event_id"]))
        data = (event.payload if event else None) or {}
        if event is not None:
            await EventService(session).mark_processed(event.event_id)
    action_id = data.get("action_id")
    if not action_id:
        return {"skipped": "no action"}
    action = await session.get(Action, uuid.UUID(action_id))
    if action is None:
        return {"skipped": "unknown action"}
    outcome = await EvidenceService(session).verify(action, dict(data.get("observed") or {}))
    return {"verdict": outcome.verdict.value}


# ── replying on the channel a run came from ────────────────────────────
async def _reply(
    session: AsyncSession, user_id: uuid.UUID, reply_to: dict | None, text: str, handle
) -> None:  # noqa: ANN001
    if not reply_to:
        return
    channel = reply_to.get("channel")
    if reply_to.get("routine_id"):
        await _record_routine(session, user_id, reply_to, text)
    try:
        if channel == "telegram":
            await _reply_telegram(session, user_id, reply_to, text, handle)
        elif channel == "call":
            await _reply_call(session, user_id, reply_to, text)
        elif channel == "app":
            from jarvis.services.notification import NotificationService
            from jarvis.workers.notify import build_senders

            await NotificationService(session, senders=build_senders(session)).notify(
                user_id, title=str(reply_to.get("title") or "Jarvis"), body=text[:400],
                data={"kind": "agent"},
            )
    except Exception as exc:  # noqa: BLE001 — the run is recorded; a lost reply is not fatal
        log.warning("agent_reply_failed", channel=channel, error=str(exc)[:200])


async def _reply_telegram(session, user_id, reply_to, text, handle) -> None:  # noqa: ANN001
    from jarvis.connectors.telegram.client import TelegramClient
    from jarvis.connectors.telegram.service import TelegramService
    from jarvis.db.models.identity import Identity

    s = get_settings()
    if not s.telegram_bot_token:
        return
    chat_id = str(reply_to.get("address") or "")
    if not chat_id:
        # A routine has no chat of its own: it speaks to the owner's linked chat.
        chat_id = await session.scalar(
            select(Identity.subject).where(
                Identity.user_id == user_id,
                Identity.provider == "telegram",
                Identity.revoked_at.is_(None),
            )
        ) or ""
    if not chat_id:
        return
    service = TelegramService(session, TelegramClient(s.telegram_bot_token))
    if handle is not None and handle.awaiting_approval:
        from jarvis.db.models.agent import Action, Approval

        approval = await session.get(Approval, uuid.UUID(handle.interrupt["approval_id"]))
        action = await session.get(Action, uuid.UUID(handle.interrupt["action_id"]))
        if approval is not None and action is not None:
            await service.send_approval_card(chat_id, approval=approval, action=action)
            return
    await service.transport.call("sendMessage", {"chat_id": chat_id, "text": text[:4000]})


async def _reply_call(session, user_id, reply_to, text) -> None:  # noqa: ANN001
    """Ring the owner's phone and speak the answer — the wake-up call (Phase 10.2.2)."""
    from jarvis.db.models.ops import NotificationEndpoint
    from jarvis.services.notification import Channel
    from jarvis.workers.notify import build_senders

    caller = build_senders(session).get(Channel.CALL)
    address = await session.scalar(
        select(NotificationEndpoint.address).where(
            NotificationEndpoint.user_id == user_id,
            NotificationEndpoint.channel == Channel.CALL.value,
            NotificationEndpoint.enabled.is_(True),
        )
    )
    if caller is None or not address:
        log.warning(
            "routine_call_unavailable", configured=caller is not None, address=bool(address)
        )
        return
    from jarvis.connectors.twilio.voice import routine_call_url

    if reply_to.get("routine_id"):
        # The script is fetched from us, so the call can offer a snooze (PLAN.md 10.2.2).
        await caller.call(address, url=routine_call_url(uuid.UUID(str(reply_to["routine_id"]))))
        return
    await caller.send(address, title=str(reply_to.get("title") or "Jarvis"), body=text[:1500])


async def _record_routine(session, user_id, reply_to, text) -> None:  # noqa: ANN001
    """Every firing leaves a chat message in the routine's own thread and an audit row,
    so the app shows what was said even when the channel was a phone call."""
    from datetime import UTC, datetime

    from jarvis.core.ids import uuid7
    from jarvis.db.models.chat import ChatMessage, Conversation
    from jarvis.db.models.ops import AuditLog, Routine

    title = str(reply_to.get("title") or "Routine")[:120]
    routine = await session.get(Routine, uuid.UUID(str(reply_to["routine_id"])))
    if routine is not None:
        routine.last_run_at = datetime.now(UTC)
        routine.last_result = text[:2000]
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.title == title,
            Conversation.archived_at.is_(None),
        )
    )
    if conversation is None:
        conversation = Conversation(id=uuid7(), user_id=user_id, title=title)
        session.add(conversation)
        await session.flush()
    conversation.last_message_at = datetime.now(UTC)
    session.add(
        ChatMessage(
            id=uuid7(),
            user_id=user_id,
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            meta={"routine_id": str(reply_to["routine_id"]), "channel": reply_to.get("channel")},
        )
    )
    session.add(
        AuditLog(
            user_id=user_id,
            actor="system",
            action="routine.finished",
            subject_type="routine",
            subject_id=str(reply_to["routine_id"]),
            detail={"title": title, "channel": reply_to.get("channel"), "excerpt": text[:200]},
        )
    )
    await session.flush()


# ── the loop ───────────────────────────────────────────────────────────
async def run_forever(*, poll_seconds: float = 2.0) -> None:
    worker_id = f"agent-{uuid.uuid4().hex[:8]}"
    async with postgres_checkpointer() as saver:
        agent = AgentWorker(saver)
        handlers = {
            "event.normalize": handle_normalize,
            "agent.run": agent.handle_run,
            "run.resume": agent.handle_resume,
            "goal.repredict": handle_repredict,
            "evidence.verify": handle_verify,
        }
        log.info("agent_worker_started", worker_id=worker_id, kinds=KINDS)
        from jarvis.workers.loop import drain

        while True:
            try:
                worked = await drain(
                    worker_id, KINDS, handlers, limit=3, visibility_seconds=900, pulse_name="agent"
                )
            except Exception as exc:  # noqa: BLE001 — the loop must survive anything
                log.error("agent_worker_tick_failed", error=str(exc)[:300])
                worked = False
            if not worked:
                await asyncio.sleep(poll_seconds)

if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
