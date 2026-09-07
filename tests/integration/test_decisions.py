"""Decision journal + outcome review (#39)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("dec@example.com", PASSWORD)
    await session.commit()
    return u


async def test_log_review_and_due(session, user):
    from datetime import UTC, datetime, timedelta

    from jarvis.db.models.domain import Decision
    from jarvis.services import decisions as svc

    d = await svc.log_decision(session, user.id, text="Take the job at GVS",
                               reasoning="better growth", expected="more responsibility",
                               review_in_days=30)
    await session.flush()
    assert d.status == "open" and d.review_at is not None

    # Not due yet.
    assert await svc.due_for_review(session, user.id) == []
    # Force the review date into the past.
    d.review_at = datetime.now(UTC) - timedelta(days=1)
    await session.flush()
    due = await svc.due_for_review(session, user.id)
    assert len(due) == 1

    reviewed = await svc.record_outcome(session, user.id, d.id, outcome="worked", note="glad I did")
    assert reviewed.status == "reviewed" and reviewed.outcome == "worked"
    assert await svc.due_for_review(session, user.id) == []  # no longer open

    with pytest.raises(ValueError, match="outcome"):
        await svc.record_outcome(session, user.id, d.id, outcome="nonsense")
    _ = Decision  # imported for clarity


async def test_endpoints(client, session):
    await IdentityService(session).register("de@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "de@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    made = (await client.post("/v1/decisions", headers=auth,
                              json={"text": "Ship the beta", "review_in_days": 14})).json()
    assert made["status"] == "open"
    listed = (await client.get("/v1/decisions", headers=auth)).json()
    assert len(listed) == 1
    done = (await client.post(f"/v1/decisions/{made['id']}/review", headers=auth,
                              json={"outcome": "mixed"})).json()
    assert done["outcome"] == "mixed"
