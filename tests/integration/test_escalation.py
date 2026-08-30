"""Phase 2.5 gate: the escalation chain.

Exit test from PLAN.md §12: an ignored alert escalates exactly **once**.

The limits here are what stop a stuck workflow becoming a 3am notification storm — the
failure that gets an assistant permanently muted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.ops import AuditLog, NotificationEndpoint
from jarvis.services.identity import IdentityService
from jarvis.services.notification import (
    Channel,
    Decision,
    NotificationService,
    UserPreferences,
    in_quiet_hours,
    plan_delivery,
)

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
IST_NOON = datetime(2026, 8, 30, 6, 30, tzinfo=UTC)      # 12:00 IST
IST_MIDNIGHT = datetime(2026, 8, 30, 18, 30, tzinfo=UTC)  # 00:00 IST


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, address, *, title, body, task_id=None):  # noqa: ANN001, ARG002
        self.sent.append((address, title))


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("esc@example.com", PASSWORD)
    session.add_all([
        NotificationEndpoint(
            user_id=u.id, channel="push", address="fcm-token-1", escalation_rank=0
        ),
        NotificationEndpoint(
            user_id=u.id, channel="telegram", address="5551234", escalation_rank=1
        ),
    ])
    await session.commit()
    return u


# ── the ladder ─────────────────────────────────────────────────────────
def _prefs(**overrides):
    base = {
        "timezone": "Asia/Kolkata",
        "quiet_hours": "22:30-07:00",
        "enabled_channels": (Channel.PUSH, Channel.TELEGRAM, Channel.CALL),
    }
    return UserPreferences(**{**base, **overrides})


def test_the_ladder_climbs_one_rung_per_attempt():
    channels = [
        plan_delivery(
            now=IST_NOON, prefs=_prefs(), attempt=i, sent_today=0, calls_today=0
        ).channel
        for i in range(3)
    ]
    assert channels == [Channel.PUSH, Channel.TELEGRAM, Channel.CALL]


def test_the_ladder_is_finite():
    """Climbing forever is a storm, not persistence."""
    plan = plan_delivery(now=IST_NOON, prefs=_prefs(), attempt=9, sent_today=0, calls_today=0)
    assert plan.decision is Decision.SUPPRESS
    assert "exhausted" in plan.reason


def test_acknowledgement_stops_the_climb():
    plan = plan_delivery(
        now=IST_NOON, prefs=_prefs(), attempt=0, sent_today=0, calls_today=0, acknowledged=True
    )
    assert plan.decision is Decision.SUPPRESS


def test_disabled_channels_are_skipped_entirely():
    prefs = _prefs(enabled_channels=(Channel.TELEGRAM,))
    plan = plan_delivery(now=IST_NOON, prefs=prefs, attempt=0, sent_today=0, calls_today=0)
    assert plan.channel is Channel.TELEGRAM


# ── quiet hours ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (IST_NOON, False),
        (IST_MIDNIGHT, True),
        (datetime(2026, 8, 30, 17, 30, tzinfo=UTC), True),   # 23:00 IST
        (datetime(2026, 8, 30, 2, 0, tzinfo=UTC), False),    # 07:30 IST
    ],
)
def test_quiet_hours_wrap_midnight_correctly(moment, expected):
    assert in_quiet_hours(moment, _prefs()) is expected


def test_quiet_hours_defer_rather_than_drop():
    """A deferred alert still arrives; a dropped one is a missed deadline."""
    plan = plan_delivery(now=IST_MIDNIGHT, prefs=_prefs(), attempt=0, sent_today=0, calls_today=0)
    assert plan.decision is Decision.DEFER
    assert plan.deliver_at is not None
    assert plan.deliver_at > IST_MIDNIGHT
    assert not in_quiet_hours(plan.deliver_at, _prefs())


def test_an_imminent_deadline_may_break_quiet_hours_only_if_allowed():
    urgent = {"due_at": IST_MIDNIGHT + timedelta(minutes=45)}
    without = plan_delivery(
        now=IST_MIDNIGHT, prefs=_prefs(), attempt=0, sent_today=0, calls_today=0, **urgent
    )
    with_override = plan_delivery(
        now=IST_MIDNIGHT,
        prefs=_prefs(allow_quiet_hours_override=True),
        attempt=0, sent_today=0, calls_today=0, **urgent,
    )
    assert without.decision is Decision.DEFER
    assert with_override.decision is Decision.SEND


def test_a_distant_deadline_never_breaks_quiet_hours():
    plan = plan_delivery(
        now=IST_MIDNIGHT,
        prefs=_prefs(allow_quiet_hours_override=True),
        attempt=0, sent_today=0, calls_today=0,
        due_at=IST_MIDNIGHT + timedelta(days=2),
    )
    assert plan.decision is Decision.DEFER


def test_no_quiet_hours_configured_means_always_allowed():
    assert not in_quiet_hours(IST_MIDNIGHT, _prefs(quiet_hours=None))


# ── caps ───────────────────────────────────────────────────────────────
def test_the_daily_cap_suppresses_further_alerts():
    plan = plan_delivery(
        now=IST_NOON, prefs=_prefs(max_per_day=5), attempt=0, sent_today=5, calls_today=0
    )
    assert plan.decision is Decision.SUPPRESS
    assert "daily cap" in plan.reason


def test_the_call_cap_falls_back_rather_than_going_silent():
    """A capped call should not silence the alert if a quieter channel remains."""
    prefs = _prefs(
        max_calls_per_day=1, enabled_channels=(Channel.CALL, Channel.TELEGRAM)
    )
    plan = plan_delivery(now=IST_NOON, prefs=prefs, attempt=0, sent_today=0, calls_today=1)
    assert plan.decision is Decision.SEND
    assert plan.channel is Channel.TELEGRAM


def test_the_call_cap_suppresses_when_nothing_quieter_remains():
    prefs = _prefs(max_calls_per_day=1, enabled_channels=(Channel.CALL,))
    plan = plan_delivery(now=IST_NOON, prefs=prefs, attempt=0, sent_today=0, calls_today=1)
    assert plan.decision is Decision.SUPPRESS


# ── the gate, through the database ─────────────────────────────────────
async def test_an_ignored_alert_escalates_exactly_once(session, user):
    push, telegram = RecordingSender(), RecordingSender()
    service = NotificationService(
        session, senders={Channel.PUSH: push, Channel.TELEGRAM: telegram}
    )

    first = await service.notify(
        user.id, title="Submit CS401", body="Due in 1h", attempt=0, now=IST_NOON
    )
    await session.commit()
    assert first.delivered and first.plan.channel is Channel.PUSH

    second = await service.notify(
        user.id, title="Submit CS401", body="Due in 1h", attempt=1, now=IST_NOON
    )
    await session.commit()
    assert second.delivered and second.plan.channel is Channel.TELEGRAM

    third = await service.notify(
        user.id, title="Submit CS401", body="Due in 1h", attempt=2, now=IST_NOON
    )
    await session.commit()
    assert not third.delivered, "the ladder ends at the configured channels"

    assert len(push.sent) == 1
    assert len(telegram.sent) == 1


async def test_acknowledging_prevents_any_further_delivery(session, user):
    push = RecordingSender()
    service = NotificationService(session, senders={Channel.PUSH: push})

    result = await service.notify(
        user.id, title="x", body="y", attempt=0, acknowledged=True, now=IST_NOON
    )
    await session.commit()
    assert not result.delivered
    assert push.sent == []


async def test_every_delivery_is_audited(session, user):
    from sqlalchemy import select

    service = NotificationService(session, senders={Channel.PUSH: RecordingSender()})
    await service.notify(user.id, title="Submit", body="Due", attempt=0, now=IST_NOON)
    await session.commit()

    entries = (await session.scalars(select(AuditLog))).all()
    sent = [e for e in entries if e.action == "notification.sent"]
    assert len(sent) == 1
    assert sent[0].detail["channel"] == "push"


async def test_the_daily_cap_is_counted_from_the_audit_log(session, user):
    from jarvis.core.config import get_settings

    settings = get_settings()
    original = settings.max_notifications_per_day
    settings.max_notifications_per_day = 2
    try:
        push = RecordingSender()
        service = NotificationService(session, senders={Channel.PUSH: push})
        for _ in range(4):
            await service.notify(user.id, title="x", body="y", attempt=0, now=IST_NOON)
            await session.commit()
        assert len(push.sent) == 2
    finally:
        settings.max_notifications_per_day = original


async def test_a_closed_task_is_not_escalated(session, user):
    from jarvis.services.goal import GoalService

    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Done already", due_at=datetime.now(UTC) + timedelta(hours=2)
    )
    await goals.update_task(user.id, task.id, status="done")
    await session.commit()

    push = RecordingSender()
    result = await NotificationService(session, senders={Channel.PUSH: push}).escalate_task(
        user.id, task.id, attempt=0, now=IST_NOON
    )
    assert not result.delivered
    assert push.sent == []
