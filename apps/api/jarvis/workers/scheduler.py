"""The scheduler tick — our replacement for EventBridge Scheduler.

Every ``tick_seconds`` it asks one question: which schedules are due? Due rows become
jobs; stale ones are stood down without alerting.

The version guard is the whole design (blueprint §7). A schedule row carries the
``task_version`` it was armed against. If the task has moved on — edited, completed,
acknowledged — the row is stale and exits silently. That is how "acknowledging cancels
every later alert" works without anything having to hunt down and delete rows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.ids import new_correlation_id
from jarvis.core.logging import get_logger
from jarvis.db.queue import JobQueue
from jarvis.db.session import session_scope

log = get_logger(__name__)

ESCALATION_PRIORITY = 10


@dataclass(frozen=True)
class TickResult:
    fired: int
    stale: int
    checked: int

    @property
    def quiet(self) -> bool:
        return self.checked == 0


class Scheduler:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.queue = JobQueue(session)

    async def tick(self, *, now: datetime | None = None, limit: int = 100) -> TickResult:
        """Fire every due schedule once.

        ``FOR UPDATE SKIP LOCKED`` so several scheduler processes can run without
        double-firing — the same mechanism the job queue uses, for the same reason.
        """
        moment = now or datetime.now(UTC)

        due = (
            await self.session.execute(
                text("""
                    SELECT s.id, s.task_id, s.task_version, s.kind, s.user_id, s.payload,
                           t.version AS current_version, t.status AS task_status,
                           t.title AS task_title, t.due_at
                    FROM schedules s
                    LEFT JOIN tasks t ON t.id = s.task_id
                    WHERE s.status = 'pending'
                      AND s.fire_at <= CAST(:now AS timestamptz)
                      AND s.id IN (
                        SELECT id FROM schedules
                        WHERE status = 'pending' AND fire_at <= CAST(:now AS timestamptz)
                        ORDER BY fire_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT :limit
                      )
                """),
                {"now": moment, "limit": limit},
            )
        ).mappings().all()

        fired = stale = 0
        for row in due:
            if self._is_stale(row):
                await self._stand_down(row["id"], _stale_reason(row))
                stale += 1
                continue
            await self._fire(row)
            fired += 1

        await self.session.flush()
        if due:
            log.info("scheduler_tick", checked=len(due), fired=fired, stale=stale)
        return TickResult(fired=fired, stale=stale, checked=len(due))

    @staticmethod
    def _is_stale(row) -> bool:  # noqa: ANN001
        if row["task_id"] is None:
            return False
        if row["current_version"] is None:
            return True
        if row["task_status"] in ("done", "cancelled"):
            return True
        return row["task_version"] != row["current_version"]

    async def _stand_down(self, schedule_id, reason: str) -> None:  # noqa: ANN001
        await self.session.execute(
            text("""
                UPDATE schedules
                SET status = 'cancelled', cancelled_reason = :reason, fired_at = now()
                WHERE id = :id
            """),
            {"id": schedule_id, "reason": reason},
        )

    async def _fire(self, row) -> None:  # noqa: ANN001
        correlation_id = new_correlation_id()

        # The idempotency key pins one job per (task, version, rung), so a tick that
        # overlaps with another cannot alert twice for the same moment.
        await self.queue.enqueue(
            "schedule.escalate",
            {
                "schedule_id": str(row["id"]),
                "task_id": str(row["task_id"]) if row["task_id"] else None,
                "task_version": row["task_version"],
                "kind": row["kind"],
                "task_title": row["task_title"],
                "due_at": row["due_at"].isoformat() if row["due_at"] else None,
            },
            user_id=row["user_id"],
            priority=ESCALATION_PRIORITY,
            idempotency_key=f"sched:{row['id']}",
            correlation_id=correlation_id,
        )
        await self.session.execute(
            text("UPDATE schedules SET status = 'fired', fired_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )


def _stale_reason(row) -> str:  # noqa: ANN001
    if row["current_version"] is None:
        return "task deleted"
    if row["task_status"] in ("done", "cancelled"):
        return f"task {row['task_status']}"
    return "task updated"


async def run_forever(*, tick_seconds: float | None = None) -> None:
    """The ``scheduler`` entrypoint of the one container (PLAN.md §6)."""
    interval = tick_seconds or get_settings().scheduler_tick_seconds
    log.info("scheduler_started", tick_seconds=interval)

    while True:
        try:
            async with session_scope() as session:
                await Scheduler(session).tick()
        except Exception as exc:  # noqa: BLE001 — a tick failing must not stop the clock
            log.error("scheduler_tick_failed", error=str(exc)[:300])
        await asyncio.sleep(interval)


if __name__ == "__main__":
    from jarvis.core.logging import configure_logging

    configure_logging(level=get_settings().log_level)
    asyncio.run(run_forever())
