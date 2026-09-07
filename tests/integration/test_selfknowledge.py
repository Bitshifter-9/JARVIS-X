"""Self-knowledge trio: mood (#18), speech profile (#12), knowledge gaps (#29)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.services.identity import IdentityService
from jarvis.services.knowledge_gaps import knowledge_gaps
from jarvis.services.mood import mood_trend, score_text
from jarvis.services.speech import speech_profile

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_mood_scorer_handles_negation():
    assert score_text("I feel great and happy")[0] == 2
    assert score_text("so stressed and tired")[0] == -2
    assert score_text("not happy")[0] == -1


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("self@example.com", PASSWORD)
    await session.commit()
    return u


async def _say(session, uid, conv_id, text, days_ago=0):
    m = ChatMessage(user_id=uid, conversation_id=conv_id, role="user", content=text)
    m.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    session.add(m)


@pytest.fixture
async def conv(session, user):
    c = Conversation(user_id=user.id, title="c")
    session.add(c)
    await session.flush()
    return c


async def test_mood_trend_buckets_by_week(session, user, conv):
    for i in range(8):
        await _say(session, user.id, conv.id,
                   "feeling good and grateful, great progress", days_ago=i)
    await session.flush()
    out = await mood_trend(session, user.id)
    assert out["enough_data"] is True
    assert out["latest"] > 0 and out["mood"] == "up"


async def test_speech_profile_finds_fillers(session, user, conv):
    for _ in range(6):
        await _say(session, user.id, conv.id, "honestly i just think basically it works, you know")
    await session.flush()
    out = await speech_profile(session, user.id)
    assert out["enough_data"] is True
    fillers = {f["term"] for f in out["fillers"]}
    assert "just" in fillers and "you know" in fillers
    assert out["avg_sentence_words"] > 0


async def test_knowledge_gaps_from_repeated_questions(session, user, conv):
    for _ in range(3):
        await _say(session, user.id, conv.id, "How do I configure Kubernetes ingress?")
    await _say(session, user.id, conv.id, "Remind me to call Sam")  # not a question
    await session.flush()
    gaps = {g["topic"] for g in await knowledge_gaps(session, user.id)}
    assert "Kubernetes" in gaps
