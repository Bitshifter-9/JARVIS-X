"""Phase 0.5 gate: the job queue.

Exit test from PLAN.md §12: *100 jobs, 4 workers, zero double-processing, poison job
dead-letters.* Everything after Phase 0 rides on this, so it is tested against a real
PostgreSQL with genuinely concurrent sessions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.job import Job, JobStatus
from jarvis.db.queue import JobQueue
from jarvis.db.session import get_sessionmaker
from sqlalchemy import func, select, text


async def _enqueue_many(count: int, kind: str = "test.work") -> None:
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        for i in range(count):
            await q.enqueue(kind, {"n": i}, idempotency_key=f"{kind}:{i}")
        await s.commit()


# ── the gate ───────────────────────────────────────────────────────────
async def test_concurrent_workers_never_double_process():
    """Four workers draining 100 jobs must each claim a disjoint set."""
    await _enqueue_many(100)

    claimed_by_worker: dict[str, list[str]] = {}

    async def worker(worker_id: str) -> None:
        mine: list[str] = []
        async with get_sessionmaker()() as s:
            q = JobQueue(s)
            while True:
                jobs = await q.claim(worker_id, limit=7)
                await s.commit()
                if not jobs:
                    break
                for job in jobs:
                    mine.append(str(job.id))
                    await q.complete(job.id, {"ok": True})
                await s.commit()
                await asyncio.sleep(0)  # yield, so the workers genuinely interleave
        claimed_by_worker[worker_id] = mine

    await asyncio.gather(*(worker(f"w{i}") for i in range(4)))

    all_claims = [job_id for claims in claimed_by_worker.values() for job_id in claims]
    assert len(all_claims) == 100, "every job must be claimed exactly once"
    assert len(set(all_claims)) == 100, "a job was claimed by two workers"

    async with get_sessionmaker()() as s:
        done = await s.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.SUCCEEDED.value)
        )
    assert done == 100

    # The work actually spread across workers rather than one winning every race.
    assert sum(1 for v in claimed_by_worker.values() if v) >= 2


async def test_poison_job_dead_letters_after_max_attempts():
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        job = await q.enqueue("test.poison", {"bad": True}, max_attempts=3)
        await s.commit()
        job_id = job.id

        for attempt in range(1, 4):
            claimed = await q.claim("w1", limit=1)
            await s.commit()
            assert len(claimed) == 1, f"attempt {attempt} should be claimable"
            status = await q.fail(job_id, f"boom on attempt {attempt}")
            await s.commit()
            if attempt < 3:
                assert status == JobStatus.PENDING.value
                # Backoff pushed it into the future; make it due again for the next round.
                await s.execute(
                    text("UPDATE jobs SET visible_at = now() - interval '1 second' WHERE id=:i"),
                    {"i": job_id},
                )
                await s.commit()

        dead = await s.get(Job, job_id)
        await s.refresh(dead)
        assert dead.status == JobStatus.DEAD_LETTERED.value
        assert dead.dead_lettered_at is not None
        assert "boom on attempt 3" in dead.last_error, "the failure evidence is retained"

        # A dead-lettered job is out of circulation, not silently retried forever.
        assert await q.claim("w1", limit=10) == []


async def test_idempotent_enqueue_collapses_replays():
    """At-least-once delivery means the same provider event arrives twice. One task results."""
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        first = await q.enqueue("test.evt", {"v": 1}, idempotency_key="gmail:msg-18c")
        await s.commit()
        second = await q.enqueue("test.evt", {"v": 2}, idempotency_key="gmail:msg-18c")
        await s.commit()

        assert first is not None
        assert second is None, "a replayed event must not enqueue a second job"
        total = await s.scalar(select(func.count()).select_from(Job))
        assert total == 1


async def test_expired_lease_is_reaped_for_another_worker():
    """A worker that dies holding a job must not strand it."""
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        await q.enqueue("test.crash", {})
        await s.commit()

        claimed = await q.claim("doomed-worker", limit=1)
        await s.commit()
        assert len(claimed) == 1
        assert await q.claim("healthy-worker", limit=1) == [], "lease should hide it"

        # Simulate the worker dying: its lease lapses without renewal.
        await s.execute(text("UPDATE jobs SET visible_at = now() - interval '1 second'"))
        await s.commit()

        assert await q.reap_expired() == 1
        await s.commit()

        recovered = await q.claim("healthy-worker", limit=1)
        await s.commit()
        assert len(recovered) == 1
        assert recovered[0].attempts == 2, "the retry is counted, not hidden"


async def test_priority_and_age_ordering():
    """Higher priority first; within a band, oldest first."""
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        now = datetime.now(UTC)
        await q.enqueue("t", {"tag": "low"}, priority=0)
        await q.enqueue("t", {"tag": "high-new"}, priority=9)
        await q.enqueue("t", {"tag": "high-old"}, priority=9, run_at=now - timedelta(minutes=5))
        await s.commit()

        order = [j.payload["tag"] for j in await q.claim("w", limit=3)]
        await s.commit()
        assert order == ["high-old", "high-new", "low"]


async def test_delayed_job_is_invisible_until_due():
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        await q.enqueue("t.later", {}, delay_seconds=3600)
        await s.commit()

        assert await q.claim("w", limit=5) == []
        stats = await q.stats()
        assert stats.pending == 1


async def test_kind_filter_is_bound_not_interpolated():
    """Workers can specialize by kind, and the filter is a bound parameter."""
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        await q.enqueue("mac.job", {})
        await q.enqueue("browser.job", {})
        await s.commit()

        got = await q.claim("w", limit=10, kinds=["browser.job"])
        await s.commit()
        assert [j.kind for j in got] == ["browser.job"]

        # A hostile "kind" is data, not SQL.
        evil = await q.claim("w", limit=10, kinds=["x'; DROP TABLE jobs; --"])
        await s.commit()
        assert evil == []
        assert await s.scalar(select(func.count()).select_from(Job)) == 2


async def test_kill_switch_cancels_pending_without_deleting_evidence():
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        await q.enqueue("t", {})
        await q.enqueue("t", {})
        await s.commit()

        assert await q.cancel_pending(reason="kill switch: demo") == 2
        await s.commit()

        stats = await q.stats()
        assert stats.cancelled == 2
        assert stats.pending == 0
        rows = (await s.execute(select(Job.last_error))).scalars().all()
        assert all(r == "kill switch: demo" for r in rows), "why it stopped is recorded"


@pytest.mark.parametrize("attempts", [1, 3, 6, 20])
async def test_backoff_is_bounded_and_jittered(attempts: int):
    from jarvis.core.config import get_settings
    from jarvis.db.queue import _backoff_seconds

    cap = get_settings().job_backoff_cap_seconds
    samples = [_backoff_seconds(attempts) for _ in range(50)]
    assert all(0 <= v <= cap for v in samples), "backoff must never exceed the cap"
    assert len(set(samples)) > 1, "identical delays would resynchronize every worker"
