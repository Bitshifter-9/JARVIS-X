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
