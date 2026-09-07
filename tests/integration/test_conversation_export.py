"""Export a conversation to a document (FEATURES-50 #18)."""

from __future__ import annotations

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("exp@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "exp@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r


async def test_export_renders_markdown_and_stores_an_artifact(client, auth, session):
    headers, _ = auth
    from jarvis.db.models.identity import User
    from sqlalchemy import select

    uid = (await session.scalars(select(User.id).where(User.email == "exp@example.com"))).one()
    conv = Conversation(user_id=uid, title="Trip planning")
    session.add(conv)
    await session.flush()
    session.add_all(
        [
            ChatMessage(user_id=uid, conversation_id=conv.id, role="user",
                        content="Where should I go?"),
            ChatMessage(user_id=uid, conversation_id=conv.id, role="assistant",
                        content="Consider Kyoto in autumn."),
        ]
    )
    await session.commit()

    r = await client.post(f"/v1/conversations/{conv.id}/export", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "# Trip planning" in body["markdown"]
    assert "**You**" in body["markdown"] and "Kyoto" in body["markdown"]
    assert body["artifact"]["url"].startswith("/v1/artifacts/")

    # The stored artifact is fetchable and matches.
    got = await client.get(body["artifact"]["url"], headers=headers)
    assert got.status_code == 200
    assert b"Kyoto" in got.content


async def test_export_refuses_someone_elses_conversation(client, auth, session):
    headers, _ = auth
    other = await IdentityService(session).register("other@example.com", PASSWORD)
    conv = Conversation(user_id=other.id, title="Private")
    session.add(conv)
    await session.commit()
    r = await client.post(f"/v1/conversations/{conv.id}/export", headers=headers)
    assert r.status_code == 404
