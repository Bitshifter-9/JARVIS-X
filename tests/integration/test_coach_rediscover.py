"""Habit coach (#31) and rediscover (#27)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import WorkSession
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("cr@example.com", PASSWORD)
    await session.commit()
    return u


async def test_habit_coach_celebrates_a_roll(session, user):
    from jarvis.services.proactivity import habit_coach

    now = datetime.now(UTC)
    for d in (0, 1, 2, 3):  # a 4-day focus streak ending today
        session.add(WorkSession(user_id=user.id, task_id=None,
                                started_at=now - timedelta(days=d),
                                active_minutes=30, source="focus"))
    await session.flush()
    coach = await habit_coach(session, user.id, tz="UTC")
    assert coach["focus"]["tone"] == "roll"
    assert "roll" in coach["focus"]["message"]


async def test_rediscover_surfaces_an_old_relevant_note(session, user):
    from jarvis.db.models.chat import ChatMessage, Conversation
    from jarvis.services.memory import HashEmbedder, MemoryService
    from jarvis.services.rediscover import rediscover

    mem = MemoryService(session, embedder=HashEmbedder())
    # An old, important note about Kubernetes (created 30 days ago).
    m = await mem.remember(user.id, content="Decided Kubernetes ingress uses nginx",
                           kind="semantic", importance=0.8)
    m.created_at = datetime.now(UTC) - timedelta(days=30)
    # The current focus mentions Kubernetes.
    conv = Conversation(user_id=user.id, title="c")
    session.add(conv)
    await session.flush()
    session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                            content="How do I scale Kubernetes now?"))
    await session.flush()

    out = await rediscover(session, user.id)
    assert "Kubernetes" in out.get("content", "")
    assert out["because"] == "kubernetes"
