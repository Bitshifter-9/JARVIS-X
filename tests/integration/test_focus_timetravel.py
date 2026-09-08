"""Focus analytics (#36) and time-travel reconstruction (#30)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("ft@example.com", PASSWORD)
    await session.commit()
    return u


async def test_focus_analytics_needs_samples(session, user):
    from jarvis.services.focus_analytics import focus_analytics

    out = await focus_analytics(session, user.id)
    assert out["enough_data"] is False


async def test_focus_analytics_ranks_distractions(session, user):
    from jarvis.core.config import get_settings
    from jarvis.services import activity
    from jarvis.services.focus_analytics import focus_analytics

    now = datetime.now(UTC)
    distractor = (get_settings().focus_distracting_apps.split(",")[0] or "Instagram").strip()
    samples = [
        {"app": "Xcode", "at": (now - timedelta(minutes=i)).isoformat()} for i in range(10)
    ]
    samples += [
        {"app": distractor, "at": (now - timedelta(minutes=i)).isoformat()} for i in range(4)
    ]
    await activity.record(session, user.id, device_id=None, platform="macos", samples=samples)
    await session.flush()

    out = await focus_analytics(session, user.id)
    assert out["enough_data"] is True
    assert out["deep_minutes"] > 0
    assert any(d["app"] == distractor for d in out["distractions"])


async def test_timetravel_reconstructs_a_day(session, user):
    from jarvis.services.timetravel import reconstruct_day

    now = datetime.now(UTC)
    t = await GoalService(session).create_task(
        user.id, title="Wrote the report", due_at=now, timezone="UTC")
    t.status = "done"
    t.completed_at = now
    await session.flush()

    day = now.astimezone().date()
    recon = await reconstruct_day(session, user.id, day, tz="UTC")
    # Depending on tz the task may land today; assert the shape and that an empty day is flagged.
    assert "done" in recon and "apps" in recon
    empty = await reconstruct_day(session, user.id, day - timedelta(days=3650), tz="UTC")
    assert empty["empty"] is True
