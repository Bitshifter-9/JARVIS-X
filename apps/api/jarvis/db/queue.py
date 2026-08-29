"""The job queue: at-least-once delivery on plain PostgreSQL.

Replaces SQS (PLAN.md §5). Four guarantees, each from a specific mechanism:

===================  =========================================================
Guarantee            Mechanism
===================  =========================================================
No double-processing ``FOR UPDATE SKIP LOCKED`` — a row claimed by one worker is
                     invisible to the others for the duration of the claim.
Crash recovery       ``visible_at`` doubles as a lease. A worker that dies holding
                     a job simply stops renewing it; ``reap_expired`` returns it.
Bounded retries      ``attempts`` against ``max_attempts``, with exponential
                     backoff and jitter between them.
Dead letters         ``dead_lettered_at`` — a real DLQ, in a column.
===================  =========================================================

The claim is a single statement. Selecting then updating in two round trips would leave a
window in which a crash loses the claim, and would double the latency of the hottest path
in the system.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import get_settings
from jarvis.core.correlation import get_correlation_id
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.db.models.job import Job, JobStatus

log = get_logger(__name__)


@dataclass(frozen=True)
class QueueStats:
    pending: int
    running: int
    succeeded: int
    failed: int
    dead_lettered: int
    cancelled: int


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff with full jitter.

    Jitter matters more than the exponent: without it, N workers that failed on the same
    upstream outage all retry at the same instant and reproduce it.
    """
    s = get_settings()
    ceiling = min(s.job_backoff_base_seconds**attempts, s.job_backoff_cap_seconds)
    return random.uniform(0, ceiling)  # noqa: S311 — jitter, not cryptography


class JobQueue:
    """Queue operations against one session. Cheap to construct; hold no state."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── producing ──────────────────────────────────────────────────────
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        user_id: Any | None = None,
        priority: int = 0,
        delay_seconds: float = 0.0,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        correlation_id: str | None = None,
    ) -> Job | None:
        """Enqueue a job. Returns ``None`` when ``idempotency_key`` already exists.

        The duplicate case is *not* an error — replaying a provider event is expected
        behaviour under at-least-once delivery, and the correct response is to do nothing.
        """
        s = get_settings()
        visible_at = run_at or datetime.now(UTC) + timedelta(seconds=delay_seconds)

        values = {
            "id": uuid7(),
            "kind": kind,
            "payload": payload or {},
            "user_id": user_id,
            "status": JobStatus.PENDING.value,
            "priority": priority,
            "visible_at": visible_at,
            "attempts": 0,
            "max_attempts": max_attempts if max_attempts is not None else s.job_max_attempts,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id or get_correlation_id(),
        }

        stmt = pg_insert(Job).values(**values)
        if idempotency_key is not None:
            # Let the unique index decide, rather than racing a SELECT-then-INSERT.
            stmt = stmt.on_conflict_do_nothing(index_elements=[Job.idempotency_key])
        stmt = stmt.returning(Job)

        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            log.debug("job_enqueue_duplicate", kind=kind, idempotency_key=idempotency_key)
        else:
            log.info("job_enqueued", job_id=str(job.id), kind=kind, priority=priority)
        return job

    # ── consuming ──────────────────────────────────────────────────────
    async def claim(
        self, worker_id: str, *, limit: int = 1, kinds: list[str] | None = None
    ) -> list[Job]:
        """Atomically claim up to ``limit`` due jobs.

        Higher ``priority`` first, then oldest ``visible_at`` — so a backlog drains in
        arrival order within each priority band rather than starving old work.
        """
        s = get_settings()

        # ``kinds`` is bound, never interpolated: a NULL parameter disables the filter.
        # No branch of this statement is built from a string, so there is no injection
        # surface even if a caller passes attacker-influenced kinds.
        stmt = text("""
            WITH claimed AS (
                SELECT id FROM jobs
                WHERE status = 'pending'
                  AND visible_at <= now()
                  AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
                ORDER BY priority DESC, visible_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE jobs SET
                status     = 'running',
                locked_by  = :worker_id,
                locked_at  = now(),
                visible_at = now() + make_interval(secs => :visibility),
                attempts   = jobs.attempts + 1,
                updated_at = now()
            FROM claimed
            WHERE jobs.id = claimed.id
            RETURNING jobs.*
        """)

        params: dict[str, Any] = {
            "limit": limit,
            "worker_id": worker_id,
            "visibility": float(s.job_visibility_timeout_seconds),
            "kinds": kinds,
        }

        rows = (await self.session.execute(stmt, params)).mappings().all()
        jobs = [await self.session.get(Job, row["id"]) for row in rows]
        claimed = [j for j in jobs if j is not None]
        for job in claimed:
            await self.session.refresh(job)
        if claimed:
            log.info("jobs_claimed", worker_id=worker_id, count=len(claimed))
        return claimed

    async def complete(self, job_id: Any, result: dict[str, Any] | None = None) -> None:
        await self.session.execute(
            text("""
                UPDATE jobs SET status='succeeded', completed_at=now(),
                       result=CAST(:result AS jsonb),
                       locked_by=NULL, last_error=NULL, updated_at=now()
                WHERE id = :id
            """),
            {"id": job_id, "result": json.dumps(result) if result else None},
        )
        log.info("job_succeeded", job_id=str(job_id))

    async def fail(self, job_id: Any, error: str, *, retry: bool = True) -> str:
        """Record a failure. Returns the resulting status.

        A job returns to ``pending`` with backoff while attempts remain; otherwise it is
        dead-lettered. Evidence of the failure is kept either way — ``last_error`` is
        never cleared by this path.
        """
        job = await self.session.get(Job, job_id)
        if job is None:
            return "missing"

        exhausted = (not retry) or job.attempts >= job.max_attempts
        truncated = error[:4000]

        if exhausted:
            await self.session.execute(
                text("""
                    UPDATE jobs SET status='dead_lettered', dead_lettered_at=now(),
                           last_error=:err, locked_by=NULL, updated_at=now()
                    WHERE id = :id
                """),
                {"id": job_id, "err": truncated},
            )
            log.warning(
                "job_dead_lettered", job_id=str(job_id), kind=job.kind,
                attempts=job.attempts, error=truncated[:200],
            )
            return JobStatus.DEAD_LETTERED.value

        delay = _backoff_seconds(job.attempts)
        await self.session.execute(
            text("""
                UPDATE jobs SET status='pending',
                       visible_at = now() + make_interval(secs => :delay),
                       last_error=:err, locked_by=NULL, updated_at=now()
                WHERE id = :id
            """),
            {"id": job_id, "delay": delay, "err": truncated},
        )
        log.info(
            "job_retry_scheduled", job_id=str(job_id), attempts=job.attempts,
            delay_seconds=round(delay, 2),
        )
        return JobStatus.PENDING.value

    # ── maintenance ────────────────────────────────────────────────────
    async def reap_expired(self) -> int:
        """Return jobs whose lease expired to the pending pool.

        This is what makes a worker crash survivable: the claim was a lease, not a
        transfer of ownership.
        """
        result = await self.session.execute(
            text("""
                UPDATE jobs SET status='pending', locked_by=NULL,
                       last_error=COALESCE(last_error, 'lease expired; worker presumed dead'),
                       updated_at=now()
                WHERE status='running' AND visible_at <= now()
                RETURNING id
            """)
        )
        count = len(result.fetchall())
        if count:
            log.warning("jobs_lease_expired_reaped", count=count)
        return count

    async def cancel_pending(
        self, *, user_id: Any | None = None, reason: str = "kill switch"
    ) -> int:
        """Cancel queued work. Used by the kill switch, which never deletes evidence."""
        # Bound, not interpolated: NULL means "every tenant".
        result = await self.session.execute(
            text("""
                UPDATE jobs SET status='cancelled', last_error=:reason, updated_at=now()
                WHERE status='pending'
                  AND (CAST(:user_id AS uuid) IS NULL OR user_id = CAST(:user_id AS uuid))
                RETURNING id
            """),
            {"reason": reason, "user_id": str(user_id) if user_id else None},
        )
        count = len(result.fetchall())
        log.warning("jobs_cancelled", count=count, reason=reason)
        return count

    async def stats(self) -> QueueStats:
        query = text("SELECT status, count(*) AS n FROM jobs GROUP BY status")
        rows = (await self.session.execute(query)).mappings().all()
        by_status = {r["status"]: r["n"] for r in rows}
        return QueueStats(
            pending=by_status.get("pending", 0),
            running=by_status.get("running", 0),
            succeeded=by_status.get("succeeded", 0),
            failed=by_status.get("failed", 0),
            dead_lettered=by_status.get("dead_lettered", 0),
            cancelled=by_status.get("cancelled", 0),
        )
