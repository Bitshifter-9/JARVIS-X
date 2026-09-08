"""Overnight agent (#45): a nightly sweep that prepares while you sleep."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.services.identity import IdentityService
from jarvis.services.overnight import last_report, overnight_sweep

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("night@example.com", PASSWORD)
    await session.commit()
    return u


async def test_sweep_runs_at_night_once(session, user):
    conv = Conversation(user_id=user.id, title="c")
    session.add(conv)
    await session.flush()
    session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                            content="I'll email the board tomorrow"))
    await session.flush()

    night = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)  # 02:00 local (UTC)
    report = await overnight_sweep(session, user.id, tz="UTC", now=night)
    assert report is not None
    assert report["commitments_caught"] >= 1
    assert (await last_report(session, user.id))["commitments_caught"] >= 1

    # Only once a night.
    assert await overnight_sweep(session, user.id, tz="UTC", now=night) is None


async def test_sweep_skips_daytime(session, user):
    day = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
    assert await overnight_sweep(session, user.id, tz="UTC", now=day) is None
