"""Interest drift: what you're into now vs drifting from (#17)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.services.identity import IdentityService
from jarvis.services.interests import interest_drift

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("drift@example.com", PASSWORD)
    await session.commit()
    return u


async def test_rising_and_fading_topics(session, user):
    now = datetime.now(UTC)
    conv = Conversation(user_id=user.id, title="c")
    session.add(conv)
    await session.flush()

    def add(text, days_ago):
        m = ChatMessage(user_id=user.id, conversation_id=conv.id, role="user", content=text)
        m.created_at = now - timedelta(days=days_ago)
        session.add(m)

    # Recent (< 30d): all about Kubernetes.
    for d in (2, 5, 9):
        add("Working on the Kubernetes migration.", d)
    # Baseline (30–120d): all about Photography, which has since dropped off.
    for d in (60, 75, 90):
        add("Editing the Photography portfolio.", d)
    await session.flush()

    drift = await interest_drift(session, user.id)
    rising = {r["term"] for r in drift["rising"]}
    fading = {f["term"] for f in drift["fading"]}
    assert "Kubernetes" in rising
    assert "Photography" in fading
    assert "Kubernetes" not in fading


async def test_endpoint(client, session):
    await IdentityService(session).register("di@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "di@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    body = (await client.get("/v1/interests", headers=auth)).json()
    assert body == {"rising": [], "fading": []}
