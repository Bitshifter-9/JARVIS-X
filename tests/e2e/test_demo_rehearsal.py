"""Phase 6.5 gate: the seven-minute demo, rehearsed.

Exit test from PLAN.md §12: *a seven-minute rehearsal succeeds twice.*

This walks the exact script in ``docs/DEMO-RUNBOOK.md``, in order, against the real
services — real Postgres, the real scheduler with its version guard, the real policy
engine, the real verifier. The DEMO CLOCK compresses waiting; it does not replace any of
the path being demonstrated.

Written as one test rather than eleven so that a break tells you *which beat* failed, the
way it would on stage.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core import demo_clock
from jarvis.core.ids import new_correlation_id
from jarvis.db.models.agent import ActionStatus, Verdict
from jarvis.db.models.job import Job
from jarvis.db.models.ops import AuditLog, NotificationEndpoint
from jarvis.services.event import EventEnvelope, EventService
from jarvis.services.event.envelope import EventSource, EventType
from jarvis.services.evidence import EvidenceService
from jarvis.services.goal import GoalService
from jarvis.services.graph import GraphService
from jarvis.services.identity import IdentityService
from jarvis.services.metrics import MetricsService
from jarvis.services.modules import ModuleService
from jarvis.services.notification import Channel, NotificationService
from jarvis.services.policy import Decision, PolicyService, ProposalContext
from jarvis.services.tool_gateway import ToolGateway
from jarvis.workers import Scheduler
from sqlalchemy import func, select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEVEN_MINUTES = 7 * 60


class Beats:
    """Records what each beat of the demo actually produced."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.log: list[tuple[str, float, str]] = []

    def record(self, name: str, detail: str) -> None:
        self.log.append((name, time.monotonic() - self.started, detail))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def report(self) -> str:
        return "\n".join(f"  {t:5.2f}s  {name}: {detail}" for name, t, detail in self.log)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, address, *, title, body, task_id=None):  # noqa: ANN001, ARG002
        self.sent.append(title)


@pytest.mark.parametrize("rehearsal", [1, 2])
async def test_the_seven_minute_demo_runs_end_to_end(session, rehearsal):
    """The full script. Run twice, because succeeding once is luck."""
    beats = Beats()
    identity = IdentityService(session)
    user = await identity.register(f"demo{rehearsal}@jarvis-x.dev", PASSWORD)
    session.add(
        NotificationEndpoint(
            user_id=user.id, channel="telegram", address="5551234", escalation_rank=0
        )
    )
    await session.commit()

    goals = GoalService(session)
    events = EventService(session)
    gateway = ToolGateway(session)
    graph = GraphService(session)

    # ── 0:30 a real deadline arrives from Gmail ────────────────────────
    source = await events.upsert_source_object(
        user_id=user.id,
        provider="gmail",
        object_id="msg-18c9f",
        kind="email",
        title="CS401 Assignment 3",
        excerpt="Assignment 3 is due on 5 September 2026 at 11:59 PM.",
        author="prof@uni.edu",
        occurred_at=datetime.now(UTC),
    )
    ingest = await events.ingest(
        EventEnvelope(
            event_type=EventType.SOURCE_MESSAGE_CHANGED,
            occurred_at=datetime.now(UTC),
            tenant_id=user.id,
            source=EventSource(provider="gmail", object_id="msg-18c9f"),
            correlation_id=new_correlation_id(),
        )
    )
    await session.commit()
    assert ingest.duplicate is False
    correlation_id = ingest.event.correlation_id
    beats.record("0:30 deadline ingested", f"correlation {correlation_id[:12]}…")

    # A redelivery, because providers do that. It must change nothing.
    replay = await events.ingest(
        EventEnvelope(
            event_type=EventType.SOURCE_MESSAGE_CHANGED,
            occurred_at=datetime.now(UTC),
            tenant_id=user.id,
            source=EventSource(provider="gmail", object_id="msg-18c9f"),
            correlation_id=correlation_id,
        )
    )
    await session.commit()
    assert replay.duplicate is True

    # ── 1:10 a sourced task, with the span it was read from ───────────
    goal = await goals.create_goal(
        user.id,
        title="Hackathon submission",
        deadline=datetime.now(UTC) + timedelta(hours=3, minutes=20),
        timezone="Asia/Kolkata",
    )
    core = await goals.create_task(
        user.id, title="Finish submission write-up", goal_id=goal.id,
        estimate_minutes=150, source_id=source.id,
        evidence_span="due on 5 September 2026 at 11:59 PM", confidence=0.94,
    )
    await goals.create_task(
        user.id, title="Record demo video", goal_id=goal.id,
        estimate_minutes=80, depends_on=[core.id],
    )
    for title, minutes in [("Alexa animation", 60), ("Knowledge-graph visualization", 70)]:
        await goals.create_task(
            user.id, title=title, goal_id=goal.id, estimate_minutes=minutes, is_optional=True
        )
    await session.commit()
    assert core.evidence_span is not None
    beats.record("1:10 sourced task", f"cites {core.evidence_span[:32]}…")

    # The goal graph, with provenance on the edge.
    await graph.assert_edge(
        user.id, subject="Hackathon submission", predicate="BLOCKED_BY",
        obj="Demo video", provenance={"source": "gmail", "object_id": "msg-18c9f"},
        confidence=0.9,
    )
    await session.commit()
    why = await graph.why(user.id, "Hackathon submission", "BLOCKED_BY", "Demo video")
    assert "gmail:msg-18c9f" in why
    beats.record("1:10 knowledge graph", why[:60] + "…")

    # ── 1:50 the prediction, and the way out ──────────────────────────
    prediction = await goals.predict_goal(user.id, goal.id)
    await session.commit()
    assert prediction.severity == "critical"
    assert prediction.options
    assert "usable minutes" in prediction.explanation
    drop = next(o for o in prediction.options if o.key == "reduce_scope")
    assert drop.probability_after > prediction.probability
    beats.record(
        "1:50 prediction",
        f"{prediction.probability:.0%} → {drop.probability_after:.0%} if scope is cut",
    )

    # ── 2:40 simulate the exact action ────────────────────────────────
    send_args = {
        "channel": "telegram", "to": "@team",
        "body": "Running late on the submission — cutting the optional Alexa animation.",
    }
    preview = await gateway.simulate(user.id, tool="message.send", args=send_args)
    assert preview.risk == "R2"
    assert "@team" in preview.recipients
    assert preview.policy_decision == "require_approval"
    beats.record("2:40 simulation", f"{preview.tool} → {preview.recipients}")

    # ── 3:20 approve, and the same hashed plan executes ───────────────
    proposal = await gateway.propose(user.id, tool="message.send", args=send_args)
    await session.commit()
    assert proposal.needs_approval

    from jarvis.core.security import approval_payload_hash

    assert proposal.approval.payload_hash == approval_payload_hash(
        tool="message.send", args=send_args, user_id=str(user.id),
        device_id=None, expires_at=proposal.action.expires_at,
    ), "the executed plan must be the plan that was previewed"

    await gateway.decide(user.id, proposal.approval.id, approved=True, decided_by="telegram")
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    assert action.status == ActionStatus.DISPATCHED.value
    beats.record("3:20 approved on phone", "dispatch authorized")

    # ── 4:10 the verifier proves it ───────────────────────────────────
    outcome = await EvidenceService(session).verify(
        action, {"provider_object_id": "tg_msg_88213"}
    )
    await session.commit()
    assert outcome.verdict is Verdict.VERIFIED
    assert action.status == ActionStatus.SUCCEEDED.value
    beats.record("4:10 verified", f"provider id tg_msg_88213, verdict {outcome.verdict.value}")

    # ── 4:45 the injected email is refused ────────────────────────────
    hostile = await PolicyService(session).evaluate(
        ProposalContext(
            user_id=user.id, tool="message.send", args=send_args, from_untrusted_source=True
        )
    )
    assert hostile.decision is Decision.DENY
    assert "read as data" in hostile.reason
    beats.record("4:45 injection blocked", hostile.reason[:56] + "…")

    # ── 5:20 the ignored alert escalates once, on the real path ───────
    urgent = await goals.create_task(
        user.id, title="Submit before the deadline",
        due_at=datetime.now(UTC) + timedelta(hours=2, minutes=5),
    )
    await session.commit()

    clock = demo_clock.enable(speed=120.0)
    try:
        assert clock.enabled
        fired = await Scheduler(session).tick(
            now=datetime.now(UTC) + timedelta(hours=1, minutes=10)
        )
        await session.commit()
        assert fired.fired >= 1, "the real scheduler path fired the T-1h rung"

        telegram = Recorder()
        notifications = NotificationService(session, senders={Channel.TELEGRAM: telegram})
        for attempt in range(3):
            await notifications.escalate_task(
                user.id, urgent.id, attempt=attempt,
                now=datetime(2026, 8, 30, 6, 30, tzinfo=UTC),
            )
            await session.commit()
        assert len(telegram.sent) == 1, "one channel is configured, so it escalates once"
        beats.record("5:20 escalation", f"{fired.fired} rung fired, 1 alert sent")
    finally:
        demo_clock.disable()

    # Acknowledging stands down everything still armed.
    cancelled = await goals.acknowledge_task(user.id, urgent.id)
    await session.commit()
    quiet = await Scheduler(session).tick(now=datetime.now(UTC) + timedelta(hours=2))
    await session.commit()
    assert quiet.fired == 0
    beats.record("5:20 acknowledged", f"{cancelled} later alerts cancelled")

    # ── 6:00 the morning brief, one surface over the same engine ──────
    brief = await ModuleService(session).morning_brief(user.id)
    assert brief.at_risk
    assert brief.suggested_first is not None
    beats.record("6:00 brief", f"first hour → {brief.suggested_first.title}")

    # ── 6:35 metrics and the kill switch ──────────────────────────────
    card = await MetricsService(session).scorecard(user.id)
    verified = next(m for m in card.metrics if m.key == "verified_tool_success")
    coverage = next(m for m in card.metrics if m.key == "approval_coverage")
    assert verified.value == 1.0
    assert coverage.value == 1.0
    beats.record("6:35 metrics", card.headline)

    from jarvis.db.queue import JobQueue

    before = await session.scalar(select(func.count()).select_from(AuditLog))
    stopped = await JobQueue(session).cancel_pending(user_id=user.id, reason="kill switch: demo")
    revoked = await identity.revoke_all_sessions(user.id)
    await session.commit()
    after = await session.scalar(select(func.count()).select_from(AuditLog))
    assert after >= before, "a kill switch never deletes evidence"
    beats.record("6:35 kill switch", f"{stopped} jobs cancelled, {revoked} sessions revoked")

    # The whole story hangs off one id.
    traced = await session.scalar(
        select(func.count()).select_from(Job).where(Job.correlation_id == correlation_id)
    )
    assert traced >= 1

    print(f"\n=== rehearsal {rehearsal} — {beats.elapsed:.2f}s ===\n{beats.report()}")
    assert beats.elapsed < SEVEN_MINUTES, "the rehearsal must fit inside the slot"
    assert len(beats.log) == 13, "every beat of the script must be reached"


async def test_the_demo_clock_leaves_the_scheduler_path_intact():
    """Compressed time must not mean a different code path."""
    clock = demo_clock.enable(speed=60.0)
    try:
        assert clock.enabled
        assert "scheduler path is unchanged" in clock.describe()
        assert clock.real_delay_for(timedelta(hours=1)) == timedelta(minutes=1)
    finally:
        demo_clock.disable()

    assert demo_clock.get_clock().enabled is False
    assert demo_clock.get_clock().real_delay_for(timedelta(hours=1)) == timedelta(hours=1)


async def test_the_demo_clock_is_refused_outside_local_environments(monkeypatch):
    """A system whose sense of time can be changed by a request has no deadlines."""
    from jarvis.core.config import get_settings
    from jarvis.core.errors import Forbidden

    monkeypatch.setattr(get_settings(), "env", "cloud")
    with pytest.raises(Forbidden):
        demo_clock.enable()


@pytest.mark.parametrize("speed", [0.5, 0.0, -1.0, 100_000.0])
async def test_an_absurd_clock_speed_is_refused(speed):
    with pytest.raises(ValueError, match="between 1 and 3600"):
        demo_clock.enable(speed=speed)
