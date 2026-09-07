"""The queue drain isolates jobs (production bug): one job's UniqueViolation must not
wedge the queue — it dead-letters after its retries while its neighbours succeed."""

from __future__ import annotations

import pytest
from jarvis.db.models.job import Job
from jarvis.db.queue import JobQueue
from jarvis.services.identity import IdentityService
from jarvis.workers.loop import drain
from sqlalchemy import select, text


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("iso@example.com", "correct-horse-battery")
    await session.commit()
    return u


async def test_a_poisoning_job_does_not_wedge_its_neighbours(session, user):
    queue = JobQueue(session)
    good = await queue.enqueue("t.good", {"n": 1}, user_id=user.id)
    bad = await queue.enqueue("t.bad", {"n": 2}, user_id=user.id, max_attempts=1)
    also = await queue.enqueue("t.good", {"n": 3}, user_id=user.id)
    await session.commit()

    ran: list[int] = []

    async def good_handler(s, job):  # noqa: ANN001
        ran.append(job.payload["n"])
        return {"ok": True}

    async def bad_handler(s, job):  # noqa: ANN001
        # A real UniqueViolation: insert the same primary key twice in one flush.
        await s.execute(
            text("INSERT INTO worker_heartbeats (name, last_tick_at, detail) "
                 "VALUES ('dupe', now(), '{}'), ('dupe', now(), '{}')")
        )
        await s.flush()
        return {"unreachable": True}

    worked = await drain(
        "w1", ["t.good", "t.bad"],
        {"t.good": good_handler, "t.bad": bad_handler},
        limit=5, pulse_name="agent",
    )
    assert worked

    async def status(job_id):  # noqa: ANN001
        async with __import__("jarvis.db.session", fromlist=["session_scope"]).session_scope() as s:
            return (await s.get(Job, job_id)).status

    assert await status(good.id) == "succeeded"
    assert await status(also.id) == "succeeded"
    assert await status(bad.id) == "dead_lettered"  # not stuck 'running'
    assert sorted(ran) == [1, 3]
    # No heartbeat row leaked from the rolled-back bad job.
    async with __import__("jarvis.db.session", fromlist=["session_scope"]).session_scope() as s:
        assert await s.scalar(
            select(Job).where(Job.id == bad.id)
        ) is not None  # the job row itself survived to carry its error


async def test_a_retryable_job_returns_to_pending_not_running(session, user):
    queue = JobQueue(session)
    job = await queue.enqueue("t.flaky", {}, user_id=user.id, max_attempts=3)
    await session.commit()

    async def flaky(s, j):  # noqa: ANN001
        raise RuntimeError("transient")

    await drain("w2", ["t.flaky"], {"t.flaky": flaky}, pulse_name="agent")
    async with __import__("jarvis.db.session", fromlist=["session_scope"]).session_scope() as s:
        row = await s.get(Job, job.id)
        assert row.status == "pending"  # will retry, never wedged
        assert row.last_error and "transient" in row.last_error
