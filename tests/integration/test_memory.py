"""Phase 2.6 gate: memory tiers and hybrid retrieval.

Exit test from PLAN.md §12: retrieval returns citations, never credentials.
"""

from __future__ import annotations

import pytest
from jarvis.db.models.ops import Memory
from jarvis.services.identity import IdentityService
from jarvis.services.memory import HashEmbedder, MemoryService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("mem@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def memory(session):
    return MemoryService(session, embedder=HashEmbedder())


async def test_a_memory_round_trips(session, user, memory):
    await memory.remember(
        user.id,
        content="Pranav prefers deep work blocks in the morning",
        kind="semantic",
        provenance={"source": "chat", "object_id": "msg-1"},
    )
    await session.commit()

    found = await memory.retrieve(user.id, "deep work blocks morning")
    assert found
    assert "deep work" in found[0].content
    assert found[0].citation == "chat:msg-1"


async def test_retrieval_never_crosses_tenants(session, user, memory):
    other = await IdentityService(session).register("intruder@example.com", PASSWORD)
    await memory.remember(user.id, content="the submission password hint is under the mat")
    await memory.remember(other.id, content="intruder private note about deadlines")
    await session.commit()

    found = await memory.retrieve(other.id, "deadlines")
    assert all("under the mat" not in r.content for r in found)
    assert len(found) <= 1


async def test_credential_shaped_content_is_refused(session, user, memory):
    """The reducer decides what is written. Refusing here beats finding a token in a
    prompt later."""
    refused = await memory.remember(
        user.id, content="my api_key is sk-live-abcdef123456 for the grading portal"
    )
    await session.commit()

    assert refused is None
    assert await session.scalar(select(Memory)) is None


async def test_long_high_entropy_strings_are_refused(session, user, memory):
    refused = await memory.remember(
        user.id, content="remember this: ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9"
    )
    await session.commit()
    assert refused is None


async def test_ordinary_content_is_not_falsely_refused(session, user, memory):
    kept = await memory.remember(
        user.id, content="The CS401 grading portal opens on 1 September"
    )
    await session.commit()
    assert kept is not None


async def test_a_correction_supersedes_rather_than_overwrites(session, user, memory):
    """The old belief stays auditable — that is what makes provenance meaningful."""
    original = await memory.remember(user.id, content="The deadline is 5 September")
    await session.commit()

    replacement = await memory.correct(
        user.id, original.id, content="The deadline is 12 September"
    )
    await session.commit()

    await session.refresh(original)
    assert original.invalidated_at is not None
    assert original.superseded_by == replacement.id
    assert replacement.provenance["corrects"] == str(original.id)

    found = await memory.retrieve(user.id, "deadline September")
    assert all(r.id != original.id for r in found), "an invalidated memory is not retrieved"


async def test_kinds_can_be_filtered(session, user, memory):
    await memory.remember(user.id, content="episodic thing happened", kind="episodic")
    await memory.remember(user.id, content="semantic thing is true", kind="semantic")
    await session.commit()

    found = await memory.retrieve(user.id, "thing", kinds=["semantic"])
    assert {r.kind for r in found} == {"semantic"}


async def test_context_is_rendered_with_citations(session, user, memory):
    await memory.remember(
        user.id,
        content="Pranav's estimates usually run 50% over",
        provenance={"source": "work_sessions", "object_id": "calib-1"},
    )
    await session.commit()

    context = await memory.context_for(user.id, "how accurate are the estimates")
    assert "Relevant memory:" in context
    assert "[work_sessions:calib-1]" in context


async def test_empty_memory_returns_empty_context(session, user, memory):
    assert await memory.context_for(user.id, "anything") == ""


async def test_reranking_prefers_a_strong_match_over_a_recent_weak_one(session, user, memory):
    await memory.remember(
        user.id, content="hackathon submission deadline is 5 September 2026", importance=0.9
    )
    await memory.remember(user.id, content="unrelated note about lunch", importance=0.1)
    await session.commit()

    found = await memory.retrieve(user.id, "hackathon submission deadline September")
    assert "hackathon submission" in found[0].content


async def test_resurface_brings_back_important_memories_on_a_curve(session, user, memory):
    from datetime import UTC, datetime, timedelta

    # An important, durable memory learned 10 days ago — due to resurface.
    await memory.remember(
        user.id,
        content="Decided the API uses cursor pagination, not offsets",
        kind="semantic",
        importance=0.8,
        provenance={"source": "chat"},
    )
    # A low-importance one and an episodic one should never resurface.
    await memory.remember(user.id, content="mentioned the weather", kind="semantic",
                          importance=0.4)
    await memory.remember(user.id, content="episodic noise", kind="episodic", importance=0.9)
    await session.commit()
    # Age them so the interval has elapsed.
    await session.execute(
        Memory.__table__.update().where(Memory.user_id == user.id).values(
            created_at=datetime.now(UTC) - timedelta(days=10))
    )
    await session.flush()

    feed = await memory.resurface(user.id)
    contents = [m["content"] for m in feed]
    assert any("cursor pagination" in c for c in contents)
    assert not any("weather" in c for c in contents)
    assert not any("episodic" in c for c in contents)

    row = (await session.scalars(
        select(Memory).where(Memory.content.like("%cursor pagination%")))).one()
    assert row.surface_count == 1 and row.last_surfaced_at is not None

    # Called again right away: the gap hasn't elapsed, so the curve doesn't advance.
    await memory.resurface(user.id)
    await session.refresh(row)
    assert row.surface_count == 1
