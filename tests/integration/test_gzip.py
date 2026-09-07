"""Large JSON answers are gzipped for clients that accept it (a phone on mobile data)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("gz@example.com", "correct-horse-battery")
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "gz@example.com", "password": "correct-horse-battery"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_big_responses_are_gzipped_when_accepted(client, auth):
    r = await client.get("/v1/routines", headers={**auth, "Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"  # the seeded built-ins exceed 1 KB
    assert isinstance(r.json(), list)  # transparently decoded
    plain = await client.get("/v1/routines", headers={**auth, "Accept-Encoding": "identity"})
    assert plain.headers.get("content-encoding") is None
