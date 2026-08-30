"""Connector authorization and the privacy surface.

Blueprint §25: disconnecting must be as easy as connecting, and must actually remove what
was stored. The state-signing tests are the ones that matter most — the callback arrives
with no session, so ``state`` is the only thing proving who started the flow.
"""

from __future__ import annotations

import uuid

import pytest
from jarvis.db.models.source import SourceAccount, SourceObject
from jarvis.services.identity import IdentityService
from sqlalchemy import func, select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client):
    await client.post(
        "/v1/auth/register", json={"email": "conn@example.com", "password": PASSWORD}
    )
    tokens = (
        await client.post(
            "/v1/auth/login", json={"email": "conn@example.com", "password": PASSWORD}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_authorize_returns_a_google_url_with_read_scopes_only(client, auth):
    body = (await client.get("/v1/connectors/google/authorize", headers=auth)).json()
    url = body["authorization_url"]

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "gmail.readonly" in url
    assert "calendar.readonly" in url
    assert "gmail.compose" not in url, "send scope is not requested by default"
    assert "access_type=offline" in url, "a refresh token is needed for background sync"


async def test_write_scopes_are_opt_in(client, auth):
    body = (
        await client.get(
            "/v1/connectors/google/authorize?include_write=true", headers=auth
        )
    ).json()
    assert "gmail.compose" in body["authorization_url"]


async def test_the_callback_refuses_an_unsigned_state(client):
    """Otherwise anyone could attach their Google account to someone else's JARVIS."""
    response = await client.get(
        "/v1/connectors/google/callback?state=made-up&code=abc", follow_redirects=False
    )
    assert response.status_code == 403


async def test_the_callback_refuses_a_state_signed_for_something_else(client):
    import jwt as pyjwt
    from jarvis.core.config import get_settings

    s = get_settings()
    forged = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "purpose": "password_reset", "exp": 9999999999},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    response = await client.get(f"/v1/connectors/google/callback?state={forged}&code=abc")
    assert response.status_code == 403


async def test_a_cancelled_authorization_is_explained_not_crashed(client, auth):
    body = (await client.get("/v1/connectors/google/authorize", headers=auth)).json()
    state = body["authorization_url"].split("state=")[1].split("&")[0]

    response = await client.get(
        f"/v1/connectors/google/callback?state={state}&error=access_denied"
    )
    assert response.status_code == 400
    assert "access_denied" in response.text


async def test_listing_shows_scopes_and_how_much_is_stored(client, auth, session):
    from jarvis.db.models.identity import User

    user = await session.scalar(select(User).where(User.email == "conn@example.com"))
    account = SourceAccount(
        user_id=user.id, provider="gmail", external_id="me@gmail.test",
        display_name="me@gmail.test",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        credentials={"access_token": "secret", "refresh_token": "also-secret"},
    )
    session.add(account)
    await session.flush()
    session.add(
        SourceObject(
            user_id=user.id, account_id=account.id, provider="gmail",
            object_id="msg-1", kind="email", title="Assignment 3",
        )
    )
    await session.commit()

    listed = (await client.get("/v1/connectors", headers=auth)).json()
    assert len(listed) == 1
    entry = listed[0]
    assert entry["provider"] == "gmail"
    assert entry["stored_objects"] == 1
    assert "gmail.readonly" in entry["scopes"][0]
    assert "credentials" not in entry, "tokens are never returned to a client"
    assert "secret" not in str(entry)


async def test_disconnecting_deletes_what_was_stored(client, auth, session):
    from jarvis.db.models.identity import User

    user = await session.scalar(select(User).where(User.email == "conn@example.com"))
    account = SourceAccount(
        user_id=user.id, provider="gmail", external_id="me@gmail.test",
        credentials={"access_token": "secret", "refresh_token": "also-secret"},
    )
    session.add(account)
    await session.flush()
    for i in range(3):
        session.add(
            SourceObject(
                user_id=user.id, account_id=account.id, provider="gmail",
                object_id=f"msg-{i}", kind="email",
            )
        )
    await session.commit()

    result = (
        await client.post(f"/v1/connectors/{account.id}/disconnect", headers=auth)
    ).json()
    assert result["objects_deleted"] == 3
    assert result["credentials_cleared"] is True

    remaining = await session.scalar(select(func.count()).select_from(SourceObject))
    assert remaining == 0
    await session.refresh(account)
    assert account.credentials == {}
    assert account.revoked_at is not None


async def test_data_can_be_kept_on_request(client, auth, session):
    """Deleting is the default, but a user who wants their history keeps it."""
    from jarvis.db.models.identity import User

    user = await session.scalar(select(User).where(User.email == "conn@example.com"))
    account = SourceAccount(user_id=user.id, provider="gmail", external_id="x")
    session.add(account)
    await session.flush()
    session.add(
        SourceObject(
            user_id=user.id, account_id=account.id, provider="gmail",
            object_id="keep-me", kind="email",
        )
    )
    await session.commit()

    result = (
        await client.post(
            f"/v1/connectors/{account.id}/disconnect?delete_data=false", headers=auth
        )
    ).json()
    assert result["objects_deleted"] == 0
    assert await session.scalar(select(func.count()).select_from(SourceObject)) == 1


async def test_you_cannot_disconnect_another_account(client, auth, session):
    other = await IdentityService(session).register("other@example.com", PASSWORD)
    account = SourceAccount(user_id=other.id, provider="gmail", external_id="theirs")
    session.add(account)
    await session.commit()

    response = await client.post(f"/v1/connectors/{account.id}/disconnect", headers=auth)
    assert response.status_code == 404
