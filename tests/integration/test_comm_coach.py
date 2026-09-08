"""Communication coach (#37): who's waiting, gone quiet, your tone — with fixes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.ops import AuditLog
from jarvis.db.models.source import SourceObject
from jarvis.services.comm_coach import comm_coach
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("cc@example.com", PASSWORD)
    await session.commit()
    return u


async def test_coach_surfaces_waiting_and_tone(session, user):
    now = datetime.now(UTC)
    # Someone waiting on a reply (triaged needs_reply, older than the grace window).
    src = SourceObject(user_id=user.id, provider="gmail", object_id="m1", kind="email",
                       title="can you review?", author="Sam <sam@x.com>",
                       occurred_at=now - timedelta(hours=8))
    session.add(src)
    await session.flush()
    session.add(AuditLog(user_id=user.id, actor="system", action="triage.classified",
                         subject_type="source_object", subject_id=str(src.id),
                         detail={"category": "needs_reply"}))
    # Enough of the user's own writing for a tone read.
    conv = Conversation(user_id=user.id, title="c")
    session.add(conv)
    await session.flush()
    for _ in range(6):
        session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                                content="Sure, let me get that done and back to you soon."))
    await session.flush()

    out = await comm_coach(session, user.id)
    kinds = {o["kind"] for o in out["observations"]}
    assert "waiting" in kinds
    assert "tone" in kinds
    assert all(o.get("fix") for o in out["observations"])
