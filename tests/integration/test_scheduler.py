"""The scheduler tick, and the version guard it enforces.

Blueprint §7: a stale scheduled event reads the current version and exits without
alerting. Everything here is about *not* alerting at the wrong moment — the failure that
teaches a user to ignore the product.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.job import Job
from jarvis.db.models.ops import Schedule
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.workers import Scheduler
from sqlalchemy import func, select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("sched@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def goals(session):
    return GoalService(session)


@pytest.fixture
def scheduler(session):
    return Scheduler(session)


async def _task_due_in(goals, user, hours: float, title: str = "Submit"):
    return await goals.create_task(
        user.id, title=title, due_at=datetime.now(UTC) + timedelta(hours=hours)
    )


async def test_nothing_due_is_a_quiet_tick(session, user, scheduler):
    result = await scheduler.tick()
    assert result.quiet
    assert result.fired == 0


async def test_a_due_schedule_becomes_a_job(session, user, goals, scheduler):
    task = await _task_due_in(goals, user, 2)
    await session.commit()

    # T-1h is not due yet; jump the clock past it.
    result = await scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1, minutes=5))
    await session.commit()

    assert result.fired == 1
    job = await session.scalar(select(Job).where(Job.kind == "schedule.escalate"))
    assert job is not None
    assert job.payload["task_id"] == str(task.id)
    assert job.payload["kind"] == "T-1h"
    assert job.priority == 10, "an escalation outranks routine work"


async def test_a_fired_schedule_does_not_fire_again(session, user, goals, scheduler):
    await _task_due_in(goals, user, 2)
    await session.commit()

    later = datetime.now(UTC) + timedelta(hours=1, minutes=5)
    first = await scheduler.tick(now=later)
    await session.commit()
    second = await scheduler.tick(now=later)
    await session.commit()

    assert first.fired == 1
    assert second.checked == 0
    assert await session.scalar(select(func.count()).select_from(Job)) == 1


# ── the version guard ──────────────────────────────────────────────────
async def test_an_acknowledged_task_never_alerts_again(session, user, goals, scheduler):
    """The gate. Acknowledging bumps the version; every armed row is now stale."""
    task = await _task_due_in(goals, user, 2)
    await session.commit()

    await goals.acknowledge_task(user.id, task.id)
    await session.commit()

    result = await scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1, minutes=55))
    await session.commit()

    assert result.fired == 0
    assert await session.scalar(select(func.count()).select_from(Job)) == 0


async def test_a_moved_deadline_stands_down_the_old_ladder(session, user, goals, scheduler):
    task = await _task_due_in(goals, user, 2)
    await session.commit()

    await goals.update_task(user.id, task.id, due_at=datetime.now(UTC) + timedelta(days=5))
    await session.commit()

    result = await scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1, minutes=55))
    await session.commit()
    assert result.fired == 0

    live = (
        await session.scalars(
            select(Schedule).where(Schedule.task_id == task.id, Schedule.status == "pending")
        )
    ).all()
    assert len(live) == 4, "the new ladder is armed for the new deadline"


async def test_a_completed_task_stands_down_rather_than_alerting(session, user, goals, scheduler):
    task = await _task_due_in(goals, user, 2)
    await session.commit()
    await goals.update_task(user.id, task.id, status="done")
    await session.commit()

    result = await scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1, minutes=55))
    await session.commit()
    assert result.fired == 0


async def test_a_stale_row_records_why_it_stood_down(session, user, goals, scheduler):
    """Silence should be explainable: the timeline must say why an alert did not happen."""
    task = await _task_due_in(goals, user, 2)
    await session.commit()

    # Arm a row against a version the task has since moved past.
    stale = Schedule(
        user_id=user.id, task_id=task.id, task_version=task.version - 1,
        kind="T-2h", fire_at=datetime.now(UTC) - timedelta(minutes=1),
        payload={"task_id": str(task.id)},
    )
    session.add(stale)
    await session.commit()

    result = await scheduler.tick()
    await session.commit()

    assert result.stale == 1
    await session.refresh(stale)
    assert stale.status == "cancelled"
    assert stale.cancelled_reason == "task updated"
    assert stale.fired_at is not None


# ── the ladder in order ────────────────────────────────────────────────
async def test_the_ladder_fires_rung_by_rung(session, user, goals, scheduler):
    await _task_due_in(goals, user, 30)
    await session.commit()

    fired: list[str] = []
    for offset in [timedelta(hours=6), timedelta(hours=28), timedelta(hours=29),
                   timedelta(hours=29, minutes=50)]:
        await scheduler.tick(now=datetime.now(UTC) + offset)
        await session.commit()
        # Explicitly ordered: an unordered SELECT makes no promise about row order, and
        # "fires rung by rung" is a claim about sequence.
        jobs = (
            await session.scalars(
                select(Job)
                .where(Job.kind == "schedule.escalate")
                .order_by(Job.created_at, Job.id)
            )
        ).all()
        fired = [j.payload["kind"] for j in jobs]

    assert fired == ["T-24h", "T-2h", "T-1h", "T-15m"]
    assert len(set(fired)) == 4, "each rung fires exactly once"


async def test_concurrent_ticks_do_not_double_fire(session, user, goals, scheduler):
    """Two scheduler processes must not alert twice for the same moment."""
    from jarvis.db.session import get_sessionmaker

    await _task_due_in(goals, user, 2)
    await session.commit()

    moment = datetime.now(UTC) + timedelta(hours=1, minutes=5)

    async def tick_once():
        async with get_sessionmaker()() as s:
            result = await Scheduler(s).tick(now=moment)
            await s.commit()
            return result.fired

    import asyncio

    counts = await asyncio.gather(tick_once(), tick_once(), tick_once())
    async with get_sessionmaker()() as s:
        total = await s.scalar(select(func.count()).select_from(Job))

    assert sum(counts) == 1
    assert total == 1


async def test_the_job_carries_the_correlation_id_forward(session, user, goals, scheduler):
    await _task_due_in(goals, user, 2)
    await session.commit()

    await scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1, minutes=5))
    await session.commit()

    job = await session.scalar(select(Job).where(Job.kind == "schedule.escalate"))
    assert job.correlation_id is not None and job.correlation_id.startswith("cor_")
