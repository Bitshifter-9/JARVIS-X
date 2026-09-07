"""Clipboard synced across devices (FEATURES-50 #26)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("clip@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "clip@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_push_then_pull_round_trips(client, auth):
    assert (await client.get("/v1/clipboard", headers=auth)).json() == {}

    r = await client.post("/v1/clipboard", headers=auth,
                          json={"text": "ssh key ABC", "device": "Mac"})
    assert r.status_code == 200 and r.json()["text"] == "ssh key ABC"

    got = (await client.get("/v1/clipboard", headers=auth)).json()
    assert got["text"] == "ssh key ABC" and got["device"] == "Mac" and got["updated_at"]
