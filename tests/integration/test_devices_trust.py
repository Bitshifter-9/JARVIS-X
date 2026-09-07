"""Devices without friction (PLAN.md 11.8): re-pairing replaces the old row, a tap in
your own app approves a device verb, and one switch trusts your devices for a while."""

from __future__ import annotations

import pytest
from jarvis.db.models.agent import Action, Approval
from jarvis.db.models.ops import Device
from jarvis.services.device import DeviceService, generate_keypair, sign
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("trust@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "trust@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _pair(session, user_id, *, name, platform="macos"):
    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user_id, name=name, platform=platform, public_key_pem=public_pem,
        allowed_bundle_ids=["com.google.Chrome"],
    )
    await session.commit()
    device = await devices.complete_pairing(
        user_id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


async def test_pairing_the_same_device_again_replaces_the_old_row(session, user):
    first = await _pair(session, user.id, name="This Mac")
    other = await _pair(session, user.id, name="This phone", platform="android")
    second = await _pair(session, user.id, name="This Mac")

    rows = {d.id: d for d in (await session.scalars(select(Device))).all()}
    assert rows[first.id].revoked_at is not None  # replaced
    assert rows[second.id].revoked_at is None
    assert rows[other.id].revoked_at is None  # a different platform is untouched


async def test_a_tap_in_the_app_is_the_approval_for_a_device_verb(client, auth, session, user):
    mac = await _pair(session, user.id, name="This Mac")
    r = await client.post(
        "/v1/actions", json={"tool": "mac.capture_screen", "args": {}, "device_id": str(mac.id)},
        headers=auth,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["approved_by"] == "app-tap"
    assert body["status"] == "queued"  # addressed to the Mac; verified when it reports back
    approval = await session.scalar(select(Approval))
    assert approval.decision == "approved" and approval.decided_by == "app-tap"
    action = await session.get(Action, approval.action_id)
    await session.refresh(action)
    assert action.status == "dispatched"

    # A tool that is not a device verb still waits, tap or no tap.
    r = await client.post(
        "/v1/actions",
        json={"tool": "message.send", "args": {"channel": "telegram", "to": "@team", "body": "x"}},
        headers=auth,
    )
    assert r.json()["status"] == "awaiting_approval"


async def test_the_dialer_is_r1_and_the_trust_switch_covers_the_hands_on_verbs(
    client, auth, session, user
):
    phone = await _pair(session, user.id, name="This phone", platform="android")
    mac = await _pair(session, user.id, name="This Mac")
    gateway = ToolGateway(session)
    # phone.call is R2 (it places the call) but rides the trust switch below.
    ring = await gateway.propose(user.id, tool="phone.ring", args={}, device_id=phone.id)
    assert ring.policy.risk.value == "R1" and not ring.needs_approval

    # Proposed by the agent (not a tap): typing on the Mac waits...
    typed = await gateway.propose(
        user.id, tool="mac.type_text", args={"bundle_id": "com.google.Chrome", "text": "hi"},
        device_id=mac.id,
    )
    assert typed.needs_approval
    await session.commit()

    # ...until the devices are trusted.
    r = await client.post("/v1/permissions/trust-devices", json={"days": 30}, headers=auth)
    assert r.status_code == 201 and r.json()["granted"] == 9
    assert (await client.get("/v1/permissions/trust-devices", headers=auth)).json()["trusted"]
    typed = await gateway.propose(
        user.id, tool="mac.type_text", args={"bundle_id": "com.google.Chrome", "text": "hi"},
        device_id=mac.id,
    )
    assert not typed.needs_approval and "standing:" in typed.policy.reason
    # Sending mail is not a device verb and never rides that switch.
    mail = await gateway.propose(
        user.id, tool="gmail.send", args={"to": "a@b.c", "subject": "s", "body": "b"}
    )
    assert mail.needs_approval

    assert (await client.delete("/v1/permissions/trust-devices", headers=auth)).status_code == 204
    assert not (await client.get("/v1/permissions/trust-devices", headers=auth)).json()["trusted"]
