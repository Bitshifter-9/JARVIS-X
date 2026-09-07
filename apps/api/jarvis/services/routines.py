"""Routines: standing instructions on a clock or a trigger (PLAN.md Phase 10.1).

A routine never runs the model itself. Firing one enqueues an ``agent.run`` job carrying
the routine's prompt as *trusted* text and, for a triggered routine, the message that
tripped it as a separate *untrusted* observation — so a mail that says "ignore your
instructions" is read, never obeyed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core.config import get_settings
from jarvis.core.cron import next_run, parse
from jarvis.core.errors import NotFound, ProblemError
from jarvis.core.logging import get_logger
from jarvis.db.models.identity import User
from jarvis.db.models.ops import Routine
from jarvis.db.queue import JOB_PRIORITY, JobQueue
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

CHANNELS = ("app", "telegram", "call")

# Shipped disabled. Enabling one is a tap; nothing rings a phone at 7am by default.
DEFAULTS: list[dict[str, Any]] = [
    {
        "kind": "morning_briefing",
        "name": "Morning briefing",
        "cron": "0 7 * * *",
        "prompt": (
            "Give me my morning briefing: the weather where I am, today's calendar, what "
            "is due today and tomorrow, anything at risk, unread mail that matters, and "
            "one suggested first task. Keep it under 120 words and speakable."
        ),
    },
    {
        "kind": "evening_review",
        "name": "Evening review",
        "cron": "0 21 * * *",
        "prompt": (
            "Give me my evening review: what got done today, what slipped, what is due "
            "tomorrow, how my screen time went (which apps ate the day), and one thing to "
            "prepare tonight. Under 110 words."
        ),
    },
    {
        "kind": "sunday_plan",
        "name": "Sunday plan",
        "cron": "0 18 * * 0",
        "prompt": (
            "Plan my week: every deadline in the next 7 days, the goals most at risk, "
            "and a suggested order of work for Monday to Friday. Under 200 words."
        ),
    },
]


class BadRoutine(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status=422, title="Invalid routine", type_="invalid-routine", detail=detail
        )


# A routine with a ``kind`` only *tells* (built-ins, or "brief" for your own): its prompt
# is answered from gathered context by the chat model. Without one it *does*: the agent.
def mode_for(row: Routine) -> str:
    return "brief" if row.kind else "agent"


class RoutineService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── reading ───────────────────────────────────────────────────────
    async def list(self, user_id: uuid.UUID) -> list[Routine]:
        rows = list(
            (
                await self.session.scalars(
                    select(Routine).where(Routine.user_id == user_id).order_by(Routine.created_at)
                )
            ).all()
        )
        if rows:
            return rows
        for spec in DEFAULTS:
            rows.append(await self.create(user_id, **spec, enabled=False))
        return rows

    async def get(self, user_id: uuid.UUID, routine_id: uuid.UUID) -> Routine:
        row = await self.session.scalar(
            select(Routine).where(Routine.id == routine_id, Routine.user_id == user_id)
        )
        if row is None:
            raise NotFound("Routine")
        return row

    # ── writing ───────────────────────────────────────────────────────
    async def create(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        prompt: str,
        cron: str | None = None,
        trigger: dict | None = None,
        channel: str = "app",
        enabled: bool = True,
        kind: str | None = None,
    ) -> Routine:
        self._validate(cron=cron, trigger=trigger, channel=channel, prompt=prompt)
        row = Routine(
            user_id=user_id,
            name=name.strip()[:120] or "Routine",
            kind=kind,
            cron=cron,
            trigger=trigger,
            prompt=prompt.strip(),
            channel=channel,
            enabled=enabled,
        )
        self.session.add(row)
        await self._reschedule(row)
        await self.session.flush()
        return row

    async def update(self, user_id: uuid.UUID, routine_id: uuid.UUID, **changes: Any) -> Routine:
        row = await self.get(user_id, routine_id)
        merged = {
            "cron": changes.get("cron", row.cron),
            "trigger": changes.get("trigger", row.trigger),
            "channel": changes.get("channel", row.channel),
            "prompt": changes.get("prompt", row.prompt),
        }
        self._validate(**merged)
        for key, value in changes.items():
            if key in ("name", "cron", "trigger", "prompt", "channel", "enabled"):
                setattr(row, key, value.strip()[:120] if key == "name" else value)
            elif key == "mode" and not (row.kind and row.kind != "brief"):
                row.kind = "brief" if value == "brief" else None
        await self._reschedule(row)
        await self.session.flush()
        return row

    async def delete(self, user_id: uuid.UUID, routine_id: uuid.UUID) -> None:
        await self.session.delete(await self.get(user_id, routine_id))
        await self.session.flush()

    @staticmethod
    def _validate(*, cron: str | None, trigger: dict | None, channel: str, prompt: str) -> None:
        if channel not in CHANNELS:
            raise BadRoutine(f"channel must be one of {', '.join(CHANNELS)}")
        if not prompt.strip():
            raise BadRoutine("prompt is required")
        if cron:
            try:
                parse(cron)
            except ValueError as exc:
                raise BadRoutine(str(exc)) from exc
        if trigger is not None and not isinstance(trigger, dict):
            raise BadRoutine("trigger must be an object")
        if not cron and not trigger:
            raise BadRoutine("a routine needs a schedule or a trigger")

    async def _reschedule(self, row: Routine, *, now: datetime | None = None) -> None:
        if not (row.cron and row.enabled):
            row.next_run_at = None
            return
        user = await self.session.get(User, row.user_id)
        zone = ZoneInfo((user.timezone if user else None) or get_settings().timezone)
        moment = (now or datetime.now(UTC)).astimezone(zone)
        row.next_run_at = next_run(row.cron, moment).astimezone(UTC)

    # ── firing ────────────────────────────────────────────────────────
    async def tick(self, *, now: datetime | None = None, limit: int = 50) -> int:
        """Fire every due cron routine once. ``SKIP LOCKED`` keeps two schedulers from
        firing the same row; the job's idempotency key keeps a retried tick from
        queueing it twice."""
        moment = now or datetime.now(UTC)
        due = (
            await self.session.execute(
                text("""
                    SELECT id FROM routines
                    WHERE enabled AND cron IS NOT NULL
                      AND next_run_at <= CAST(:now AS timestamptz)
                    ORDER BY next_run_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                """),
                {"now": moment, "limit": limit},
            )
        ).scalars().all()
        fired = 0
        for routine_id in due:
            row = await self.session.get(Routine, routine_id)
            if row is None:
                continue
            slot = row.next_run_at
            await self.fire(row, key=f"routine:{row.id}:{slot.isoformat() if slot else 'now'}")
            await self._reschedule(row, now=moment)
            fired += 1
        if fired:
            await self.session.flush()
            log.info("routines_fired", count=fired)
        return fired

    async def fire(
        self,
        row: Routine,
        *,
        key: str | None = None,
        context: str | None = None,
        delay_seconds: float = 0.0,
    ) -> uuid.UUID | None:
        payload = {
            "user_id": str(row.user_id),
            "text": row.prompt,
            "source": "routine",
            "trust": "trusted",
            "trigger": "routine",
            "mode": mode_for(row),
            "reply_to": {
                "channel": row.channel,
                "routine_id": str(row.id),
                "title": row.name,
            },
        }
        if context:
            payload["context"] = context[:8000]
        job = await JobQueue(self.session).enqueue(
            "agent.run",
            payload,
            user_id=row.user_id,
            priority=JOB_PRIORITY["background"],
            delay_seconds=delay_seconds,
            idempotency_key=key,
            max_attempts=2,
        )
        return job.id if job else None

    async def on_message(
        self,
        user_id: uuid.UUID,
        *,
        provider: str,
        author: str | None,
        title: str | None,
        body: str | None,
    ) -> int:
        """Fire every enabled triggered routine whose filter matches this message."""
        rows = (
            await self.session.scalars(
                select(Routine).where(
                    Routine.user_id == user_id,
                    Routine.enabled.is_(True),
                    Routine.trigger.is_not(None),
                )
            )
        ).all()
        fired = 0
        for row in rows:
            if not _trigger_matches(row.trigger or {}, provider, author, title):
                continue
            context = f"From: {author or '?'}\nSubject: {title or ''}\n\n{body or ''}"
            await self.fire(row, context=context)
            fired += 1
        return fired


def _trigger_matches(trigger: dict, provider: str, author: str | None, title: str | None) -> bool:
    want_provider = str(trigger.get("provider") or "").lower()
    if want_provider and want_provider != provider.lower():
        return False
    for key, haystack in (("from", author), ("subject", title)):
        needle = str(trigger.get(key) or "").strip().lower()
        if needle and needle not in (haystack or "").lower():
            return False
    return True
