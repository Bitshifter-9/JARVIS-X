"""Relationship cadence: who you keep up with, who you've gone quiet on (#16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.source import SourceObject
from jarvis.services.identity import IdentityService
from jarvis.services.relationships import relationships

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("rel@example.com", PASSWORD)
    await session.commit()
    return u


def _msg(uid, oid, author, when):
    return SourceObject(user_id=uid, provider="gmail", object_id=oid, kind="email",
                        title="hi", author=author, occurred_at=when)


async def test_flags_someone_you_went_quiet_on(session, user):
    now = datetime.now(UTC)
    # Priya: weekly for months, but the last was 5 weeks ago → quiet.
    for i, d in enumerate((70, 63, 56, 49, 42, 35)):
        session.add(_msg(user.id, f"p{i}", "Priya <priya@x.com>", now - timedelta(days=d)))
    # Sam: contacted just yesterday and regularly → not quiet.
    for i, d in enumerate((21, 14, 7, 1)):
        session.add(_msg(user.id, f"s{i}", "Sam <sam@x.com>", now - timedelta(days=d)))
    # A one-off sender never becomes a relationship.
    session.add(_msg(user.id, "o1", "Once <once@x.com>", now - timedelta(days=3)))
    await session.flush()

    people = {r["name"]: r for r in await relationships(session, user.id)}
    assert "Priya" in people and "Sam" in people
    assert "Once" not in people  # below the min-contacts threshold

    assert people["Priya"]["quiet"] is True
    assert people["Priya"]["cadence_days"] == 7
    assert people["Sam"]["quiet"] is False
    # Quiet-and-important sorts to the top.
    assert (await relationships(session, user.id))[0]["name"] == "Priya"


async def test_endpoint(client, session):
    await IdentityService(session).register("re@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "re@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/v1/relationships", headers=auth)).json() == []
