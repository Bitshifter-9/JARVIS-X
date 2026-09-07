"""Sign in with Google survives the middle of the flow.

The app starts, the browser finishes, the app polls. Between the first and the last
step a deploy can restart the API — the attempt must be in the database, not in a dict.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.api.routes import auth as auth_routes
from jarvis.api.routes import connectors as connectors_routes
from jarvis.connectors.google.oauth import GoogleTokens
from jarvis.db.models.identity import PendingLogin, User
from sqlalchemy import select


@pytest.fixture
def google(monkeypatch):
    async def fake_exchange(code):  # noqa: ANN001, ANN202
        return GoogleTokens(
            access_token="at",
            refresh_token="rt",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=["openid"],
            email="signin@gmail.com",
        )

    async def fake_userinfo(access_token):  # noqa: ANN001, ANN202
        return {"email": "signin@gmail.com", "name": "Sign In"}

    monkeypatch.setattr(connectors_routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth_routes, "_userinfo", fake_userinfo)


async def test_start_poll_finish_poll_creates_an_account_and_a_session(client, session, google):
    start = (await client.get("/v1/auth/google/start")).json()
    state = start["poll_token"]
    assert state.startswith("glogin_") and f"state={state}" in start["authorization_url"]

    pending = await client.post("/v1/auth/google/poll", json={"poll_token": state})
    assert pending.json() == {"pending": True}

    # The browser lands on the connector callback with the login state.
    page = await client.get(f"/v1/connectors/google/callback?state={state}&code=x")
    assert page.status_code == 200 and "jarvisx://signed-in" in page.text

    done = (await client.post("/v1/auth/google/poll", json={"poll_token": state})).json()
    assert done["pending"] is False and done["access_token"] and done["refresh_token"]
    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {done['access_token']}"}
    )
    assert me.json()["email"] == "signin@gmail.com"

    # Consumed: a replayed poll gets nothing.
    assert (
        await client.post("/v1/auth/google/poll", json={"poll_token": state})
    ).status_code == 401
    assert (await session.scalar(select(PendingLogin).where(PendingLogin.state == state))) is None


async def test_the_attempt_lives_in_the_database_not_the_process(client, session, google):
    state = (await client.get("/v1/auth/google/start")).json()["poll_token"]
    row = await session.scalar(select(PendingLogin).where(PendingLogin.state == state))
    assert row is not None and row.tokens is None


async def test_an_expired_attempt_is_refused_everywhere(client, session, google):
    state = (await client.get("/v1/auth/google/start")).json()["poll_token"]
    row = await session.scalar(select(PendingLogin).where(PendingLogin.state == state))
    row.created_at = datetime.now(UTC) - timedelta(minutes=6)
    await session.commit()

    assert (
        await client.post("/v1/auth/google/poll", json={"poll_token": state})
    ).status_code == 401
    page = await client.get(f"/v1/connectors/google/callback?state={state}&code=x")
    assert page.status_code == 403


async def test_a_second_sign_in_reuses_the_account(client, session, google):
    for _ in range(2):
        state = (await client.get("/v1/auth/google/start")).json()["poll_token"]
        await client.get(f"/v1/connectors/google/callback?state={state}&code=x")
        await client.post("/v1/auth/google/poll", json={"poll_token": state})
    users = (await session.scalars(select(User).where(User.email == "signin@gmail.com"))).all()
    assert len(users) == 1
