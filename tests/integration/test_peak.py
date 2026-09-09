"""Peak-performance coach: context-switch tax, the 3M breaks, biological peak."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.identity import IdentityService
from jarvis.services.peak import longest_gap, peak_report, switches_per_hour

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_switch_tax_counts_changes_not_samples():
    # Ten samples, two genuine changes of attention.
    assert switches_per_hour(["a"] * 5 + ["b"] * 3 + ["a"] * 2, 60) == 2.0
    # Thrashing between two apps is a switch every sample.
    assert switches_per_hour(["a", "b"] * 15, 60) == 29.0
    assert switches_per_hour(["a"], 60) == 0.0


def test_longest_gap_is_the_break():
    base = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    assert longest_gap([base, base + timedelta(minutes=2), base + timedelta(hours=3)]) == timedelta(
        hours=2, minutes=58
    )


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("peak@example.com", PASSWORD)
    await session.commit()
    return u


async def test_report_needs_real_sampling(session, user):
    assert (await peak_report(session, user.id))["enough_data"] is False


async def test_a_day_of_thrashing_with_no_break(session, user):
    from jarvis.services import activity

    now = datetime(2026, 9, 9, 17, 0, tzinfo=UTC)
    # Two hours of alternating apps, sampled every 30 s, never stepping away.
    samples = []
    start = now - timedelta(hours=2)
    for i in range(240):
        samples.append({
            "app": "Slack" if i % 2 else "Xcode",
            "at": (start + timedelta(seconds=30 * i)).isoformat(),
        })
    await activity.record(session, user.id, device_id=None, platform="macos", samples=samples)
    await session.flush()

    out = await peak_report(session, user.id, tz="UTC", now=now)
    assert out["enough_data"] is True
    assert out["switches_per_hour"] > 20          # constant task switching
    assert "does not run two tasks" in out["switch_note"]
    assert out["breaks"]["micro_due"] is True     # never stepped away for 10 minutes
    assert out["breaks"]["meso_due"] is True
    assert out["breaks"]["macro_due"] is True


async def test_a_real_break_clears_the_micro_flag(session, user):
    from jarvis.services import activity

    now = datetime(2026, 9, 9, 17, 0, tzinfo=UTC)
    start = now - timedelta(hours=3)
    samples = [
        {"app": "Xcode", "at": (start + timedelta(seconds=30 * i)).isoformat()}
        for i in range(40)
    ]
    # A genuine 40-minute absence, then back.
    resume = start + timedelta(minutes=60)
    samples += [
        {"app": "Xcode", "at": (resume + timedelta(seconds=30 * i)).isoformat()}
        for i in range(40)
    ]
    await activity.record(session, user.id, device_id=None, platform="macos", samples=samples)
    await session.flush()

    out = await peak_report(session, user.id, tz="UTC", now=now)
    assert out["breaks"]["micro_due"] is False
    assert out["breaks"]["longest_today_minutes"] >= 20
