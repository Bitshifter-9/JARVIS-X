"""Media diary: what you watched/read, with a takeaway (#6)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jarvis.db.models.source import SourceObject
from jarvis.services.identity import IdentityService
from jarvis.services.media_diary import media_diary, set_note

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("md@example.com", PASSWORD)
    await session.commit()
    return u


async def test_diary_lists_and_annotates_and_is_searchable(session, user):
    from jarvis.services.search import life_search

    obj = SourceObject(user_id=user.id, provider="reading", object_id="r1", kind="video",
                       title="GraphRAG explained", url="https://youtu.be/x",
                       occurred_at=datetime.now(UTC))
    session.add(obj)
    await session.flush()

    diary = await media_diary(session, user.id)
    assert len(diary) == 1 and diary[0]["note"] is None

    updated = await set_note(session, user.id, obj.id, "The temporal graph idea is the key insight")
    assert "temporal graph" in updated["note"]

    # The takeaway is now recall-able.
    hits = await life_search(session, user.id, "temporal graph")
    assert any("GraphRAG" in (h.get("title") or "") for h in hits)


async def test_note_rejects_a_non_reading_source(session, user):
    obj = SourceObject(user_id=user.id, provider="gmail", object_id="m1", kind="email",
                       title="hi", occurred_at=datetime.now(UTC))
    session.add(obj)
    await session.flush()
    assert await set_note(session, user.id, obj.id, "note") is None
