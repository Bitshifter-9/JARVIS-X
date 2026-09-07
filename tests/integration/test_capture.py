"""Quick-capture: a dated thought → deadline, else → memory (second-brain #9)."""

from __future__ import annotations

import pytest
from jarvis.db.models.domain import Task
from jarvis.db.models.ops import Memory
from jarvis.services.identity import IdentityService
from jarvis.services.memory.embeddings import HashEmbedder, set_embedder
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture(autouse=True)
def _fast_embedder():
    # The local model isn't downloaded in CI; the hash stand-in exercises the same path.
    set_embedder(HashEmbedder())


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("cap@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "cap@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_a_dated_thought_becomes_a_deadline(client, auth, session):
    r = await client.post("/v1/capture", json={"text": "pay rent friday 6pm"}, headers=auth)
    assert r.status_code == 200 and r.json()["kind"] == "task"
    assert (await session.scalars(select(Task))).first() is not None


async def test_a_plain_thought_becomes_a_searchable_memory(client, auth, session):
    r = await client.post(
        "/v1/capture", json={"text": "idea: a kettle that texts when it boils"}, headers=auth
    )
    assert r.status_code == 200 and r.json()["kind"] == "note"
    mem = (await session.scalars(select(Memory))).first()
    assert mem is not None and "kettle" in mem.content

    # It's then findable in life search.
    found = (await client.get("/v1/search?q=kettle", headers=auth)).json()["results"]
    assert any(x["type"] == "memory" for x in found)


async def test_memory_consolidates_near_duplicates(session):
    """mem0-style: storing the same thought twice keeps one memory and reinforces it,
    instead of bloating the store (storage budget)."""
    from jarvis.db.models.identity import User
    from jarvis.services.memory import MemoryService
    from sqlalchemy import func

    user = await IdentityService(session).register("dup@example.com", PASSWORD)
    await session.commit()
    svc = MemoryService(session)

    a = await svc.remember(user.id, content="Pranav prefers dark mode", kind="semantic")
    b = await svc.remember(user.id, content="Pranav prefers dark mode", kind="semantic")
    await session.commit()

    assert a is not None and b is not None and a.id == b.id  # consolidated, not duplicated
    assert b.importance > 0.5  # reinforced
    count = await session.scalar(
        select(func.count()).select_from(User).where(User.id == user.id)
    )
    assert count == 1
    mem_count = (await session.scalars(select(Memory).where(Memory.user_id == user.id))).all()
    assert len(mem_count) == 1


async def test_prune_forgets_stale_unused_episodic_but_keeps_facts(session):
    """The 'forget' tier: an episodic memory never reinforced by recall ages out; a
    reinforced one and any semantic fact are kept (second-brain optimisation)."""
    from datetime import UTC, datetime, timedelta

    from jarvis.services.memory import MemoryService
    from sqlalchemy import text

    user = await IdentityService(session).register("ttl@example.com", PASSWORD)
    await session.commit()
    svc = MemoryService(session)

    stale = await svc.remember(user.id, content="ate a sandwich tuesday", kind="episodic")
    fact = await svc.remember(user.id, content="timezone is IST", kind="semantic")
    kept = await svc.remember(user.id, content="watched a great film", kind="episodic")
    assert stale and fact and kept

    old = datetime.now(UTC) - timedelta(days=60)
    await session.execute(
        text("UPDATE memories SET created_at = :o WHERE user_id = CAST(:u AS uuid)"),
        {"o": old, "u": str(user.id)},
    )
    # A recalled memory earns its keep (importance above the default).
    await session.execute(
        text("UPDATE memories SET importance = 0.8 WHERE id = CAST(:i AS uuid)"),
        {"i": str(kept.id)},
    )
    await session.flush()

    forgotten = await svc.prune()
    rows = (await session.scalars(select(Memory).where(Memory.user_id == user.id))).all()
    remaining = {m.content for m in rows}
    assert forgotten == 1
    assert "ate a sandwich tuesday" not in remaining  # forgotten
    assert "timezone is IST" in remaining  # semantic fact kept
    assert "watched a great film" in remaining  # reinforced, kept
