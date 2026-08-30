"""Phase 6.2 gate: chaos.

Exit test from PLAN.md §12: **known failures repair once or stop clearly.**

The failure mode this suite exists to prevent is not the crash — it is the silent
degradation: a retry loop that never ends, a job that vanishes when a worker dies, a
provider outage that looks like an empty result rather than an outage.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core.config import get_settings
from jarvis.db.models.agent import ActionStatus
from jarvis.db.models.domain import Task
from jarvis.db.models.job import Job, JobStatus
from jarvis.db.models.ops import Device, Schedule
from jarvis.db.models.source import Event
from jarvis.db.queue import JobQueue
from jarvis.db.session import get_sessionmaker
from jarvis.llm import (
    AllProvidersFailed,
    CallClass,
    LLMRequest,
    LLMRouter,
    Message,
    ProviderRateLimited,
    ProviderTransientError,
)
from jarvis.services.device import DeviceService, generate_keypair, sign
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from sqlalchemy import func, select, text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("chaos@example.com", PASSWORD)
    await session.commit()
    return u


# ── provider outage ────────────────────────────────────────────────────
class FlakyProvider:
    def __init__(self, name, *, fail_times=0, error=None, is_paid=False):
        self.name = name
        self.model = f"{name}-model"
        self.is_paid = is_paid
        self.remaining_failures = fail_times
        self.error = error or ProviderTransientError(f"{name} is down")
        self.calls = 0

    def is_configured(self):
        return True

    def supports(self, call_class):  # noqa: ARG002
        return True

    async def generate(self, request):  # noqa: ARG002
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise self.error
        from jarvis.llm.types import LLMResponse

        return LLMResponse(text="ok", provider=self.name, model=self.model)


def _router(session, providers, order):
    return LLMRouter(
        session,
        providers={p.name: p for p in providers},
        cascade=dict.fromkeys(CallClass, order),
    )


def _request():
    return LLMRequest(call_class=CallClass.CHAT, messages=[Message("user", "hi")])


async def test_a_total_provider_outage_fails_loudly_with_reasons(session):
    """An outage must not look like an empty answer."""
    providers = [
        FlakyProvider("groq", fail_times=99, error=ProviderRateLimited("429")),
        FlakyProvider("gemini", fail_times=99),
    ]
    router = _router(session, providers, ("groq", "gemini"))

    with pytest.raises(AllProvidersFailed) as exc:
        await router.generate(_request())
    await session.commit()

    assert set(exc.value.failures) == {"groq", "gemini"}
    assert all(reason for reason in exc.value.failures.values())


async def test_one_provider_flapping_does_not_take_the_system_down(session):
    providers = [FlakyProvider("groq", fail_times=1), FlakyProvider("gemini")]
    router = _router(session, providers, ("groq", "gemini"))

    first = await router.generate(_request())
    await session.commit()
    assert first.provider == "gemini"

    # Groq recovers; the breaker has not tripped after a single failure.
    second = await router.generate(_request())
    await session.commit()
    assert second.provider == "groq"


async def test_a_persistently_failing_provider_stops_being_dialled(session, monkeypatch):
    """The scarce resource is a rate-limited provider's remaining quota."""
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_failure_threshold", 2)
    monkeypatch.setattr(settings, "provider_cooldown_seconds", 300)

    broken = FlakyProvider("groq", fail_times=99, error=ProviderRateLimited("429"))
    healthy = FlakyProvider("gemini")
    router = _router(session, [broken, healthy], ("groq", "gemini"))

    for _ in range(4):
        await router.generate(_request())
        await session.commit()

    assert broken.calls == 2, "the breaker opened and stayed open"
    assert healthy.calls == 4


async def test_recovery_closes_the_breaker(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "provider_failure_threshold", 2)

    provider = FlakyProvider("groq", fail_times=2)
    router = _router(session, [provider], ("groq",))

    for _ in range(2):
        with pytest.raises(AllProvidersFailed):
            await router.generate(_request())
        await session.commit()

    await router.health.reset("groq")
    await session.commit()

    assert (await router.generate(_request())).provider == "groq"


# ── worker crash ───────────────────────────────────────────────────────
async def test_a_worker_dying_mid_job_does_not_lose_the_job():
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        await q.enqueue("chaos.work", {})
        await s.commit()

        claimed = await q.claim("doomed", limit=1)
        await s.commit()
        assert len(claimed) == 1

        # The worker vanishes without completing or failing: its lease simply lapses.
        await s.execute(text("UPDATE jobs SET visible_at = clock_timestamp() - interval '1s'"))
        await s.commit()

        assert await q.reap_expired() == 1
        await s.commit()

        recovered = await q.claim("healthy", limit=1)
        await s.commit()
        assert len(recovered) == 1
        assert recovered[0].attempts == 2


async def test_a_job_that_always_fails_stops_rather_than_looping():
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        job = await q.enqueue("chaos.poison", {}, max_attempts=3)
        await s.commit()

        for _ in range(5):
            claimed = await q.claim("w", limit=1)
            await s.commit()
            if not claimed:
                break
            await q.fail(job.id, "always broken")
            await s.commit()
            await s.execute(
                text("UPDATE jobs SET visible_at = clock_timestamp() - interval '1s' "
                     "WHERE id = :i AND status = 'pending'"),
                {"i": job.id},
            )
            await s.commit()

        final = await s.get(Job, job.id)
        await s.refresh(final)
        assert final.status == JobStatus.DEAD_LETTERED.value
        assert final.attempts <= final.max_attempts


async def test_concurrent_workers_survive_a_thundering_herd():
    """Twelve workers, one queue, no double-processing."""
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        for i in range(120):
            await q.enqueue("chaos.herd", {"i": i}, idempotency_key=f"herd:{i}")
        await s.commit()

    claimed_ids: list[str] = []

    async def worker(name: str):
        async with get_sessionmaker()() as s:
            q = JobQueue(s)
            while True:
                jobs = await q.claim(name, limit=5)
                await s.commit()
                if not jobs:
                    return
                for job in jobs:
                    claimed_ids.append(str(job.id))
                    await q.complete(job.id)
                await s.commit()
                await asyncio.sleep(0)

    await asyncio.gather(*(worker(f"w{i}") for i in range(12)))
    assert len(claimed_ids) == 120
    assert len(set(claimed_ids)) == 120


# ── duplicate delivery ─────────────────────────────────────────────────
async def test_a_provider_redelivering_ten_times_produces_one_event(session, user):
    from jarvis.core.ids import new_correlation_id, new_event_id
    from jarvis.services.event import EventEnvelope, EventService
    from jarvis.services.event.envelope import EventSource, EventType

    service = EventService(session)
    for _ in range(10):
        await service.ingest(
            EventEnvelope(
                event_id=new_event_id(),
                event_type=EventType.SOURCE_MESSAGE_CHANGED,
                occurred_at=datetime.now(UTC),
                tenant_id=user.id,
                source=EventSource(provider="gmail", object_id="msg-storm"),
                correlation_id=new_correlation_id(),
            )
        )
        await session.commit()

    assert await session.scalar(select(func.count()).select_from(Event)) == 1
    assert await session.scalar(select(func.count()).select_from(Job)) == 1


# ── stale schedules ────────────────────────────────────────────────────
async def test_a_schedule_for_a_moved_deadline_does_not_fire(session, user):
    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Submit", due_at=datetime.now(UTC) + timedelta(days=2)
    )
    await session.commit()
    original_version = task.version

    await goals.update_task(
        user.id, task.id, due_at=datetime.now(UTC) + timedelta(days=6)
    )
    await session.commit()

    stale = (
        await session.scalars(
            select(Schedule).where(
                Schedule.task_id == task.id, Schedule.task_version == original_version
            )
        )
    ).all()
    assert stale, "the old schedules still exist as a record"
    assert all(s.status == "cancelled" for s in stale)


async def test_a_schedule_for_a_finished_task_does_not_fire(session, user):
    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Submit", due_at=datetime.now(UTC) + timedelta(days=2)
    )
    await session.commit()

    await goals.update_task(user.id, task.id, status="done")
    await session.commit()

    pending = await session.scalar(
        select(func.count())
        .select_from(Schedule)
        .where(Schedule.task_id == task.id, Schedule.status == "pending")
    )
    assert pending == 0


async def test_rapid_edits_leave_exactly_one_live_ladder(session, user):
    """Editing a deadline five times must not arm twenty alerts."""
    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Submit", due_at=datetime.now(UTC) + timedelta(days=2)
    )
    await session.commit()

    for days in range(3, 8):
        await goals.update_task(
            user.id, task.id, due_at=datetime.now(UTC) + timedelta(days=days)
        )
        await session.commit()

    live = (
        await session.scalars(
            select(Schedule).where(Schedule.task_id == task.id, Schedule.status == "pending")
        )
    ).all()
    current = (await session.get(Task, task.id)).version
    assert len(live) == 4
    assert {s.task_version for s in live} == {current}


# ── Mac disconnection ──────────────────────────────────────────────────
async def test_a_mac_going_offline_queues_rather_than_fails(session, user):
    from jarvis.services.tool_gateway import ToolGateway

    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id, name="Mac", public_key_pem=public_pem,
        allowed_bundle_ids=["com.google.Chrome"],
    )
    await session.commit()
    device = await devices.complete_pairing(
        user.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await devices.connect(device.id, "conn-1")
    await session.commit()

    proposal = await ToolGateway(session).propose(
        user.id, tool="mac.open_app",
        args={"bundle_id": "com.google.Chrome"}, device_id=device.id,
    )
    await session.commit()

    await devices.disconnect(device.id, "conn-1")
    await session.commit()
    assert await devices.is_online(device.id) is False

    dispatchable, needs_review = await devices.pending_for_device(device.id)
    await session.commit()
    assert [a.id for a in dispatchable] == [proposal.action.id]
    assert needs_review == []


async def test_a_job_that_went_stale_offline_is_never_run_late(session, user):
    from jarvis.services.tool_gateway import ToolGateway

    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id, name="Mac", public_key_pem=public_pem,
        allowed_bundle_ids=["com.google.Chrome"],
    )
    await session.commit()
    device = await devices.complete_pairing(
        user.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()

    proposal = await ToolGateway(session).propose(
        user.id, tool="mac.open_app",
        args={"bundle_id": "com.google.Chrome"}, device_id=device.id,
    )
    await session.commit()

    await session.execute(
        text("UPDATE actions SET expires_at = now() - interval '2 hours' WHERE id = :i"),
        {"i": proposal.action.id},
    )
    await session.commit()

    dispatchable, needs_review = await devices.pending_for_device(device.id)
    await session.commit()
    assert dispatchable == []
    assert [a.id for a in needs_review] == [proposal.action.id]
    assert needs_review[0].status == ActionStatus.EXPIRED.value


async def test_a_revoked_device_cannot_reconnect(session, user):
    from jarvis.core.errors import Forbidden

    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(user.id, name="Mac", public_key_pem=public_pem)
    await session.commit()
    device = await devices.complete_pairing(
        user.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await devices.revoke(user.id, device.id, reason="stolen")
    await session.commit()

    with pytest.raises(Forbidden):
        await devices.connect(device.id, "conn-2")
    assert (await session.get(Device, device.id)).is_active is False


# ── kill switch under load ─────────────────────────────────────────────
async def test_the_kill_switch_stops_queued_work_and_keeps_the_record(session, user):
    async with get_sessionmaker()() as s:
        q = JobQueue(s)
        for i in range(20):
            await q.enqueue("chaos.pending", {"i": i}, user_id=user.id)
        await s.commit()

        cancelled = await q.cancel_pending(user_id=user.id, reason="kill switch: chaos")
        await s.commit()
        assert cancelled == 20

        stats = await q.stats()
        assert stats.pending == 0
        assert stats.cancelled == 20

        reasons = (await s.execute(select(Job.last_error))).scalars().all()
        assert all(r == "kill switch: chaos" for r in reasons)
