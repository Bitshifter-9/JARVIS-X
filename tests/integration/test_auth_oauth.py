"""Phase 0.3 + 0.4: the API skeleton and the OAuth2 authorization server.

Exit tests from PLAN.md §12:
* ``/healthz`` returns the build SHA; every response carries a correlation id.
* register -> authorize -> token -> refresh -> revoke.

The OAuth failure cases below are all attacks that have worked against real
implementations, so each gets its own test rather than a shared happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core.security import hash_token, pkce_challenge
from jarvis.db.session import get_sessionmaker
from jarvis.services.identity import IdentityService
from sqlalchemy import text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


# ── 0.3 skeleton ───────────────────────────────────────────────────────
async def test_healthz_reports_build_and_correlation_id(client):
    response = await client.get("/healthz")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["build"], "a response must always identify its build"
    assert body["correlation_id"].startswith("cor_")
    assert response.headers["X-Correlation-ID"] == body["correlation_id"]


async def test_inbound_correlation_id_is_adopted_when_well_formed(client):
    supplied = "cor_01J9ZQK5N8XW7YVBTC3MHD2FGP"
    response = await client.get("/healthz", headers={"X-Correlation-ID": supplied})
    assert response.json()["correlation_id"] == supplied


async def test_malformed_correlation_id_is_replaced_not_echoed(client):
    """A caller must not be able to inject arbitrary text into every downstream log line."""
    response = await client.get(
        "/healthz", headers={"X-Correlation-ID": "'; DROP TABLE jobs; --"}
    )
    cid = response.json()["correlation_id"]
    assert cid.startswith("cor_") and len(cid) == 30


async def test_readyz_checks_the_database(client):
    body = (await client.get("/readyz")).json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"


async def test_errors_are_rfc9457_problem_documents(client):
    response = await client.get("/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")

    body = response.json()
    assert body["status"] == 401
    assert body["type"].endswith("/unauthorized")
    assert body["correlation_id"].startswith("cor_")


async def test_validation_errors_are_problem_documents_too(client):
    response = await client.post("/v1/auth/register", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation-error")
    assert body["errors"]


# ── 0.4 first-party sessions ───────────────────────────────────────────
async def test_register_login_me_refresh_logout(client):
    registered = await client.post(
        "/v1/auth/register",
        json={"email": "Pranav@Example.com", "password": PASSWORD, "display_name": "Pranav"},
    )
    assert registered.status_code == 201
    assert registered.json()["email"] == "pranav@example.com", "email is normalised"

    login = await client.post(
        "/v1/auth/login", json={"email": "pranav@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    tokens = login.json()

    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["display_name"] == "Pranav"

    refreshed = await client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"], "refresh tokens rotate"

    # The rotated-out token is dead, so a stolen copy stops working at the next refresh.
    replay = await client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401

    assert (
        await client.post("/v1/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    ).status_code == 204


async def test_duplicate_registration_is_a_conflict(client):
    body = {"email": "dupe@example.com", "password": PASSWORD}
    assert (await client.post("/v1/auth/register", json=body)).status_code == 201
    conflict = await client.post("/v1/auth/register", json=body)
    assert conflict.status_code == 409


async def test_wrong_password_and_unknown_account_are_indistinguishable(client):
    await client.post(
        "/v1/auth/register", json={"email": "real@example.com", "password": PASSWORD}
    )
    wrong = await client.post(
        "/v1/auth/login", json={"email": "real@example.com", "password": "wrong-password-x"}
    )
    missing = await client.post(
        "/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"], "no account enumeration"


async def test_short_passwords_are_rejected(client):
    response = await client.post(
        "/v1/auth/register", json={"email": "weak@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_revoke_all_sessions_kills_every_refresh_token(client):
    await client.post("/v1/auth/register", json={"email": "k@example.com", "password": PASSWORD})
    first = (
        await client.post("/v1/auth/login", json={"email": "k@example.com", "password": PASSWORD})
    ).json()
    second = (
        await client.post("/v1/auth/login", json={"email": "k@example.com", "password": PASSWORD})
    ).json()

    killed = await client.post(
        "/v1/auth/sessions/revoke-all",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert killed.json()["revoked"] == 2

    for tokens in (first, second):
        response = await client.post(
            "/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 401


# ── 0.4 OAuth2 authorization-code + PKCE ───────────────────────────────
@pytest.fixture
async def alexa_client_and_user(client):
    """A public client (Alexa-style) plus a registered account."""
    await client.post("/v1/auth/register", json={"email": "o@example.com", "password": PASSWORD})
    async with get_sessionmaker()() as s:
        await IdentityService(s).register_client(
            client_id="alexa-skill",
            name="JARVIS X for Alexa",
            redirect_uris=["https://layla.amazon.com/api/skill/link/ABC123"],
            scopes=["tasks.read", "goals.read"],
            is_public=True,
        )
        await s.commit()
    return "alexa-skill", "https://layla.amazon.com/api/skill/link/ABC123"


async def _authorize(client, client_id, redirect_uri, **overrides):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "tasks.read",
        "state": "xyz-state",
        "code_challenge": pkce_challenge(VERIFIER),
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return await client.get("/oauth/authorize", params=params)


async def test_authorization_page_renders_after_validation(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    response = await _authorize(client, client_id, redirect_uri)
    assert response.status_code == 200
    assert "JARVIS X for Alexa" in response.text
    assert "tasks.read" in response.text


async def test_unregistered_redirect_uri_never_reaches_a_password_form(
    client, alexa_client_and_user
):
    """Exact-match only: a prefix match turns a client-side open redirect into token theft."""
    client_id, redirect_uri = alexa_client_and_user
    response = await _authorize(
        client, client_id, f"{redirect_uri}.attacker.example.com"
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "password" not in response.text.lower()


async def test_public_client_must_use_pkce(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    response = await _authorize(client, client_id, redirect_uri, code_challenge="")
    assert response.status_code == 400
    assert "PKCE is required" in response.json()["error_description"]


async def test_public_client_may_not_use_plain_pkce(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    response = await _authorize(
        client, client_id, redirect_uri, code_challenge=VERIFIER, code_challenge_method="plain"
    )
    assert response.status_code == 400


async def test_full_code_exchange_then_refresh(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user

    submitted = await client.post(
        "/oauth/authorize",
        data={
            "email": "o@example.com",
            "password": PASSWORD,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "tasks.read",
            "state": "xyz-state",
            "code_challenge": pkce_challenge(VERIFIER),
            "code_challenge_method": "S256",
        },
    )
    assert submitted.status_code == 303
    location = submitted.headers["location"]
    assert location.startswith(redirect_uri)
    assert "state=xyz-state" in location, "state must round-trip for CSRF protection"

    code = location.split("code=")[1].split("&")[0]

    tokens = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": VERIFIER,
        },
    )
    assert tokens.status_code == 200
    body = tokens.json()
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "tasks.read"

    # The issued access token works against the API.
    me = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json()["email"] == "o@example.com"

    # ...and the refresh grant works too, which is what keeps the link alive.
    refreshed = await client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


async def _issue_code(client, client_id, redirect_uri) -> str:
    submitted = await client.post(
        "/oauth/authorize",
        data={
            "email": "o@example.com",
            "password": PASSWORD,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "tasks.read",
            "code_challenge": pkce_challenge(VERIFIER),
            "code_challenge_method": "S256",
        },
    )
    return submitted.headers["location"].split("code=")[1].split("&")[0]


async def test_authorization_code_is_single_use(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    code = await _issue_code(client, client_id, redirect_uri)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": VERIFIER,
    }
    assert (await client.post("/oauth/token", data=data)).status_code == 200

    replay = await client.post("/oauth/token", data=data)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


async def test_wrong_pkce_verifier_is_rejected(client, alexa_client_and_user):
    """Without this check, an intercepted code is enough to steal the session."""
    client_id, redirect_uri = alexa_client_and_user
    code = await _issue_code(client, client_id, redirect_uri)

    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": "a-different-verifier-entirely-0000000000000",
        },
    )
    assert response.status_code == 400
    assert "PKCE" in response.json()["error_description"]


async def test_redirect_uri_must_match_the_one_in_the_authorization(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    code = await _issue_code(client, client_id, redirect_uri)

    # The code is bound to the redirect_uri it was issued for, so swapping it at the
    # token endpoint fails even though the client and code are both genuine.
    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://evil.example.com/cb",
            "code_verifier": VERIFIER,
        },
    )
    assert response.status_code == 400


async def test_expired_code_is_rejected(client, alexa_client_and_user):
    client_id, redirect_uri = alexa_client_and_user
    code = await _issue_code(client, client_id, redirect_uri)

    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE oauth_codes SET expires_at = :t WHERE code_hash = :h"),
            {"t": datetime.now(UTC) - timedelta(seconds=1), "h": hash_token(code)},
        )
        await s.commit()

    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": VERIFIER,
        },
    )
    assert response.status_code == 400
    assert "expired" in response.json()["error_description"].lower()


async def test_bad_password_returns_to_the_form_not_the_redirect_uri(client, alexa_client_and_user):
    """A failed login must not be observable from the relying party."""
    client_id, redirect_uri = alexa_client_and_user
    response = await client.post(
        "/oauth/authorize",
        data={
            "email": "o@example.com",
            "password": "wrong-password-here",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": pkce_challenge(VERIFIER),
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/oauth/authorize")
    assert "code=" not in response.headers["location"]


async def test_confidential_client_must_present_its_secret(client):
    await client.post("/v1/auth/register", json={"email": "c@example.com", "password": PASSWORD})
    async with get_sessionmaker()() as s:
        await IdentityService(s).register_client(
            client_id="mac-node",
            name="JARVIS X Mac node",
            redirect_uris=["http://127.0.0.1:7717/callback"],
            is_public=False,
            client_secret="super-secret-value",
        )
        await s.commit()

    submitted = await client.post(
        "/oauth/authorize",
        data={
            "email": "c@example.com",
            "password": PASSWORD,
            "client_id": "mac-node",
            "redirect_uri": "http://127.0.0.1:7717/callback",
            "response_type": "code",
        },
    )
    code = submitted.headers["location"].split("code=")[1].split("&")[0]

    base = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "mac-node",
        "redirect_uri": "http://127.0.0.1:7717/callback",
    }
    missing = await client.post("/oauth/token", data=base)
    assert missing.status_code == 401

    wrong = await client.post("/oauth/token", data=base | {"client_secret": "nope"})
    assert wrong.status_code == 401

    good = await client.post("/oauth/token", data=base | {"client_secret": "super-secret-value"})
    assert good.status_code == 200


async def test_unsupported_grant_type_is_reported_as_such(client):
    response = await client.post("/oauth/token", data={"grant_type": "password"})
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"
