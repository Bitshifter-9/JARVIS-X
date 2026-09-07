"""The queue drain, done once and correctly.

The bug this fixes, seen in production: a worker claimed up to N jobs and ran them all
in **one** session. When one job's flush hit a ``UniqueViolationError``, the session was
poisoned, so ``queue.fail`` could not record the failure and the whole tick rolled back —
the job stayed ``running`` with an expired lease, was reaped, and retried forever.

Here each job runs in its **own** transaction. One job's failure cannot touch another's,
and a failure is always recorded on a clean session, so a job dead-letters after its
retries instead of wedging the queue.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from jarvis.core.logging import get_logger
from jarvis.core.overrides import apply_overrides
from jarvis.db.models.job import Job
from jarvis.db.queue import JobQueue
from jarvis.db.session import session_scope
from jarvis.workers.pulse import pulse

log = get_logger(__name__)

Handler = Callable[..., Awaitable[dict]]


async def drain(
    worker_id: str,
    kinds: list[str],
    handlers: dict[str, Handler],
    *,
    limit: int = 3,
    visibility_seconds: float | None = None,
    pulse_name: str,
) -> bool:
    """Claim due jobs, then run each one in isolation. Returns whether any ran."""
    # 1) Claim in one short transaction; committing here makes the lease durable.
    async with session_scope() as session:
        await apply_overrides(session)
        await pulse(session, pulse_name)
        queue = JobQueue(session)
        await queue.reap_expired()
        claimed = await queue.claim(
            worker_id, limit=limit, kinds=kinds, visibility_seconds=visibility_seconds
        )
        job_ids = [j.id for j in claimed]

    # 2) One transaction per job, so a poisoned session never spreads.
    for job_id in job_ids:
        await _run_one(job_id, handlers)
    return bool(job_ids)


async def _run_one(job_id: uuid.UUID, handlers: dict[str, Handler]) -> None:
    try:
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != "running":
                return  # reaped, cancelled, or already done by another worker
            outcome = await handlers[job.kind](session, job)
            await JobQueue(session).complete(job_id, outcome)
    except Exception as exc:  # noqa: BLE001 — the failure is recorded on its own session
        log.error("job_failed", job_id=str(job_id), error=f"{type(exc).__name__}: {exc}"[:300])
        try:
            async with session_scope() as fail_session:
                job = await fail_session.get(Job, job_id)
                kind = job.kind if job else "?"
                await JobQueue(fail_session).fail(job_id, f"{type(exc).__name__}: {exc}")
            log.warning("job_dead_or_retry_recorded", job_id=str(job_id), kind=kind)
        except Exception as inner:  # noqa: BLE001 — last resort; the lease will reap it
            log.error("job_fail_record_failed", job_id=str(job_id), error=str(inner)[:200])
