"""Habit streaks and meeting prep (FEATURES-50 #33, #38)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from jarvis.db.models.domain import WorkSession
from jarvis.db.models.source import SourceObject
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.services.proactivity import habit_streaks, meeting_prep, streak_of

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_streak_of_counts_current_and_longest():
    today = date(2026, 9, 7)
    # today, yesterday, and a broken older run of three.
    days = {today, today - timedelta(days=1),
            date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)}
    assert streak_of(days, today=today) == {"current": 2, "longest": 3}

    # A one-day gap is grace: a streak ending yesterday still counts.
    assert streak_of({today - timedelta(days=1)}, today=today)["current"] == 1
    # Two days stale breaks it.
    assert streak_of({today - timedelta(days=2)}, today=today)["current"] == 0
    assert streak_of(set(), today=today) == {"current": 0, "longest": 0}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("pro@example.com", PASSWORD)
    await session.commit()
    return u


async def test_focus_streak_from_work_sessions(session, user):
    now = datetime.now(UTC)
    for d in (0, 1, 2):  # three consecutive days ending today
        session.add(
            WorkSession(
                user_id=user.id,
                task_id=None,
                started_at=now - timedelta(days=d),
                active_minutes=25,
                source="focus",
            )
        )
    await session.flush()
    streaks = await habit_streaks(session, user.id, tz="UTC")
    assert streaks["focus"]["current"] == 3


async def test_meeting_prep_finds_the_next_event_and_context(session, user):
    now = datetime.now(UTC)
    session.add_all(
        [
            SourceObject(
                user_id=user.id, provider="gcal", object_id="ev1", kind="calendar_event",
                title="Project Phoenix sync", author="lead@acme.com",
                occurred_at=now + timedelta(hours=3),
            ),
            SourceObject(
                user_id=user.id, provider="gmail", object_id="mp1", kind="email",
                title="Phoenix agenda", author="lead@acme.com", excerpt="see attached",
                occurred_at=now - timedelta(days=1),
            ),
        ]
    )
    await GoalService(session).create_task(
        user.id, title="Prep slides", due_at=now + timedelta(hours=2), timezone="UTC"
    )
    await session.flush()

    prep = await meeting_prep(session, user.id, tz="UTC")
    assert prep and prep["title"] == "Project Phoenix sync"
    assert prep["organizer"] == "lead@acme.com"
    assert any(m["subject"] == "Phoenix agenda" for m in prep["related_mail"])
    assert any(t["title"] == "Prep slides" for t in prep["nearby_deadlines"])
    assert "phoenix" in prep["keywords"]


async def test_meeting_prep_is_none_without_an_upcoming_event(session, user):
    assert await meeting_prep(session, user.id, tz="UTC") is None
