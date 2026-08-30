"""Pairing over the real HTTP API, exactly as ``python -m macnode pair`` does it."""

from __future__ import annotations

import pytest
from jarvis.services.device.keys import generate_keypair, sign

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
CHROME = "com.google.Chrome"


@pytest.fixture
async def auth(client):
    await client.post("/v1/auth/register", json={"email": "mac@example.com", "password": PASSWORD})
    tokens = (
        await client.post("/v1/auth/login", json={"email": "mac@example.com", "password": PASSWORD})
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_pair_a_mac_over_http(client, auth):
    private_pem, public_pem = generate_keypair()

    started = await client.post(
        "/v1/devices/pair",
        json={
            "name": "Pranav's MacBook",
            "public_key_pem": public_pem,
            "allowed_bundle_ids": [CHROME],
            "capabilities": ["mac.open_app"],
        },
        headers=auth,
    )
    assert started.status_code == 200
    challenge = started.json()["challenge"]

    completed = await client.post(
        "/v1/devices/pair/complete",
        json={"challenge": challenge, "signature": sign(private_pem, challenge.encode())},
        headers=auth,
    )
    assert completed.status_code == 200
    device = completed.json()
    assert device["paired"] is True
    assert device["online"] is False, "pairing is not connecting"
    assert device["allowed_bundle_ids"] == [CHROME]


async def test_a_wrong_signature_does_not_pair(client, auth):
    _private, public_pem = generate_keypair()
    attacker_private, _ = generate_keypair()

    started = await client.post(
        "/v1/devices/pair", json={"name": "Mac", "public_key_pem": public_pem}, headers=auth
    )
    challenge = started.json()["challenge"]

    response = await client.post(
        "/v1/devices/pair/complete",
        json={"challenge": challenge, "signature": sign(attacker_private, challenge.encode())},
        headers=auth,
    )
    assert response.status_code == 403


async def test_the_server_publishes_its_public_key_only(client, auth):
    """The helper needs it to verify jobs; the private half must never appear."""
    response = await client.get("/v1/devices/server-key", headers=auth)
    assert response.status_code == 200
    pem = response.json()["public_key_pem"]
    assert "BEGIN PUBLIC KEY" in pem
    assert "PRIVATE" not in pem


async def test_revoking_a_device_shows_it_revoked(client, auth):
    private_pem, public_pem = generate_keypair()
    started = await client.post(
        "/v1/devices/pair", json={"name": "Mac", "public_key_pem": public_pem}, headers=auth
    )
    challenge = started.json()["challenge"]
    device = (
        await client.post(
            "/v1/devices/pair/complete",
            json={"challenge": challenge, "signature": sign(private_pem, challenge.encode())},
            headers=auth,
        )
    ).json()

    revoked = await client.post(
        f"/v1/devices/{device['id']}/revoke?reason=lost", headers=auth
    )
    assert revoked.json()["revoked"] is True

    listed = (await client.get("/v1/devices", headers=auth)).json()
    assert listed[0]["revoked"] is True


async def test_devices_are_scoped_to_their_owner(client, auth):
    _private, public_pem = generate_keypair()
    await client.post(
        "/v1/devices/pair", json={"name": "Mac", "public_key_pem": public_pem}, headers=auth
    )

    await client.post(
        "/v1/auth/register", json={"email": "other@example.com", "password": PASSWORD}
    )
    other = (
        await client.post(
            "/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD}
        )
    ).json()

    listed = (
        await client.get(
            "/v1/devices", headers={"Authorization": f"Bearer {other['access_token']}"}
        )
    ).json()
    assert listed == []


async def test_telegram_webhook_rejects_a_bad_secret(client):
    """Always 200 — a non-200 makes Telegram retry, and retrying a hostile request
    helps nobody."""
    from jarvis.core.config import get_settings

    settings = get_settings()
    settings.telegram_webhook_secret = "the-real-secret"  # noqa: S105
    try:
        response = await client.post(
            "/webhooks/telegram",
            json={"callback_query": {"id": "1", "data": "approve:x",
                                     "message": {"chat": {"id": "1"}}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert response.status_code == 200
    finally:
        settings.telegram_webhook_secret = ""
