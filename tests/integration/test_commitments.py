"""Commitment tracking: catch the promises the user made (second-brain #22)."""

from __future__ import annotations

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.domain import Commitment
from jarvis.services.commitment import detect_commitment, scan_commitments
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_detect_commitment_reads_first_person_promises():
    assert detect_commitment("I'll send the report by Friday") is not None
    assert detect_commitment("ok. I need to call the bank tomorrow. thanks") == \
        "I need to call the bank tomorrow"
    assert detect_commitment("let me get back to you next week") is not None
    # A request to someone else is not the user's own commitment.
    assert detect_commitment("can you send me the file by Monday?") is None
    assert detect_commitment("the weather is nice today") is None


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("commit@example.com", PASSWORD)
    await session.commit()
    return u


async def test_scan_catches_commitments_from_chat_and_dedupes(session, user):
    conv = Conversation(user_id=user.id, title="chat")
    session.add(conv)
    await session.flush()
    for text in [
        "remind me, I'll email Priya on Friday",
        "the meeting was fine",  # not a commitment
        "I need to renew my passport",
    ]:
        session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                                content=text))
    await session.flush()

    caught = await scan_commitments(session, user.id)
    assert caught == 2
    # Re-scanning the same messages catches nothing new.
    assert await scan_commitments(session, user.id) == 0

    rows = (await session.scalars(select(Commitment).where(Commitment.user_id == user.id))).all()
    texts = {c.text for c in rows}
    assert any("Priya" in t for t in texts)
    # The dated one got a due date.
    assert any(c.due_at is not None for c in rows)


async def test_the_endpoints(client, session):
    await IdentityService(session).register("ce@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "ce@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "ce@example.com"))).one()
        conv = Conversation(user_id=uid, title="c")
        s.add(conv)
        await s.flush()
        s.add(ChatMessage(user_id=uid, conversation_id=conv.id, role="user",
                          content="I promise to review the PR tonight"))
        await s.commit()

    assert (await client.post("/v1/commitments/scan", headers=auth)).json()["caught"] == 1
    listed = (await client.get("/v1/commitments", headers=auth)).json()
    assert len(listed) == 1 and "review the PR" in listed[0]["text"]

    cid = listed[0]["id"]
    done = (await client.post(f"/v1/commitments/{cid}/done", headers=auth)).json()
    assert done["status"] == "done"
    assert (await client.get("/v1/commitments", headers=auth)).json() == []  # no open ones left
