"""Life search: one box over everything captured (second-brain #26)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.ops import Memory
from jarvis.db.models.source import SourceObject
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.services.search import life_search

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("search@example.com", PASSWORD)
    await session.commit()
    return u


async def test_search_spans_mail_deadlines_chat_and_memory(session, user):
    now = datetime.now(UTC)
    session.add(
        SourceObject(
            user_id=user.id, provider="gmail", object_id="m1", kind="email",
            title="Project Phoenix kickoff", author="lead@acme.com",
            excerpt="the phoenix launch is Friday", occurred_at=now,
        )
    )
    await GoalService(session).create_task(
        user.id, title="Prep Phoenix slides", due_at=now, timezone="UTC"
    )
    conv = Conversation(user_id=user.id, title="chat")
    session.add(conv)
    await session.flush()
    session.add(
        ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                    content="remind me about phoenix next week")
    )
    session.add(
        Memory(user_id=user.id, kind="semantic", content="Phoenix is Pranav's side project")
    )
    await session.flush()

    results = await life_search(session, user.id, "phoenix")
    types = {r["type"] for r in results}
    assert {"mail", "deadline", "chat", "memory"} <= types
    # Every hit carries a route to open it and a snippet.
    assert all(r["route"] and "id" in r for r in results)
    assert any("phoenix" in (r["snippet"] or "").lower() for r in results)


async def test_short_or_empty_query_returns_nothing(session, user):
    assert await life_search(session, user.id, "a") == []
    assert await life_search(session, user.id, "  ") == []


async def test_the_endpoint(client, session):
    await IdentityService(session).register("se@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "se@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    body = (await client.get("/v1/search?q=anything", headers=auth)).json()
    assert body == {"query": "anything", "results": []}
    # Too-short query is rejected by validation.
    assert (await client.get("/v1/search?q=a", headers=auth)).status_code == 422
