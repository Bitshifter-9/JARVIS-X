"""MLFQ (PLAN.md 12.5): an interactive command is claimed before background work."""

from __future__ import annotations

import pytest
from jarvis.db.queue import JOB_PRIORITY, JobQueue
from jarvis.services.identity import IdentityService


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("mlfq@example.com", "correct-horse-battery")
    await session.commit()
    return u


async def test_interactive_jobs_are_claimed_before_background_ones(session, user):
    queue = JobQueue(session)
    # A long background research job is already queued...
    await queue.enqueue(
        "agent.run", {"n": "routine"}, user_id=user.id, priority=JOB_PRIORITY["background"]
    )
    # ...then the owner types a command.
    await queue.enqueue(
        "agent.run", {"n": "telegram"}, user_id=user.id, priority=JOB_PRIORITY["interactive"]
    )
    await session.commit()

    first = await queue.claim("w", limit=1, kinds=["agent.run"])
    assert first[0].payload["n"] == "telegram"  # the person waiting wins
    second = await queue.claim("w", limit=1, kinds=["agent.run"])
    assert second[0].payload["n"] == "routine"


def test_the_bands_are_ordered_interactive_over_background():
    assert JOB_PRIORITY["interactive"] > JOB_PRIORITY["decision"] > JOB_PRIORITY["background"]
