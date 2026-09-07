"""Rhythm model: when you focus and when you slump (#15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import WorkSession
from jarvis.services.identity import IdentityService
from jarvis.services.rhythm import rhythm

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("rhythm@example.com", PASSWORD)
    await session.commit()
    return u


async def test_needs_enough_data(session, user):
    out = await rhythm(session, user.id, tz="UTC")
    assert out["enough_data"] is False


async def test_finds_the_peak_window(session, user):
    # Ten focus sessions, all started around 10:00 UTC on recent days.
    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    for i in range(10):
        session.add(WorkSession(
            user_id=user.id, task_id=None,
            started_at=base + timedelta(days=i, minutes=i * 2),
            active_minutes=30, source="focus",
        ))
    await session.flush()

    out = await rhythm(session, user.id, tz="UTC", now=datetime(2026, 9, 20, tzinfo=UTC))
    assert out["enough_data"] is True
    assert out["samples"] == 10
    assert out["peak_hour"] == 10
    # The best 3h window covers the 10:00 hour.
    w = out["best_window"]
    assert w["start"] <= 10 < (w["end"] if w["end"] > w["start"] else w["end"] + 24)
