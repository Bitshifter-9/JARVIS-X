"""The new device verbs (PLAN.md 12.8): call/ring/locate/whatsapp-send tiers, and that
the chat agent can address them to the paired device on its own."""

from __future__ import annotations

import pytest
from jarvis.services.device import DeviceService, generate_keypair, sign
from jarvis.services.identity import IdentityService
from jarvis.services.policy.rules import manifest_for, rule_for
from jarvis.services.tool_gateway import ToolGateway

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("verbs@example.com", PASSWORD)
    await session.commit()
    return u


async def _phone(session, user_id):
    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user_id, name="Pixel", platform="android", public_key_pem=public_pem
    )
    await session.commit()
    phone = await devices.complete_pairing(
        user_id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return phone


def test_the_verbs_have_the_right_tier_and_evidence():
    assert rule_for("phone.ring").risk.value == "R1"
    assert rule_for("mac.ring").risk.value == "R1"
    assert rule_for("phone.locate").risk.value == "R2"   # location leaves the device
    assert rule_for("phone.call").risk.value == "R2"     # it actually calls now
    assert rule_for("phone.whatsapp_send").risk.value == "R2"  # sends on its own
    assert manifest_for("phone.whatsapp_send").verify == ("key_pressed",)
    assert manifest_for("phone.locate").verify == ("http_status",)
    assert rule_for("phone.system_info").risk.value == "R1"
    assert rule_for("phone.torch").risk.value == "R1"
    assert rule_for("phone.media").risk.value == "R1"  # play/pause/skip is harmless
    for tool in (
        "phone.ring", "mac.ring", "phone.locate", "phone.call", "phone.whatsapp_send",
        "phone.system_info", "phone.torch", "phone.media",
    ):
        assert manifest_for(tool) is not None


async def test_the_agent_addresses_a_ring_to_the_paired_phone_without_a_device_id(session, user):
    from jarvis.services.agent.executor import ToolExecutor

    phone = await _phone(session, user.id)
    # The agent proposes phone.ring with no device_id (as it would from a chat request).
    proposal = await ToolGateway(session).propose(user.id, tool="phone.ring", args={})
    await session.commit()
    assert not proposal.needs_approval  # R1
    action = await ToolGateway(session).authorize_dispatch(proposal.action.id)
    observed = await ToolExecutor(session).run(action)
    assert observed["queued_for_device"] == str(phone.id)  # addressed on its own


async def test_ring_is_covered_by_the_trust_switch_but_locate_and_call_wait(client, session, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "verbs@example.com", "password": PASSWORD}
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await _phone(session, user.id)
    # Before trust: locate (R2) waits.
    locate = await ToolGateway(session).propose(user.id, tool="phone.locate", args={})
    assert locate.needs_approval
    await session.commit()

    trusted = await client.post("/v1/permissions/trust-devices", json={"days": 7}, headers=auth)
    assert trusted.status_code == 201
    locate = await ToolGateway(session).propose(user.id, tool="phone.locate", args={})
    assert not locate.needs_approval and "standing:" in locate.policy.reason
    wa = await ToolGateway(session).propose(
        user.id, tool="phone.whatsapp_send", args={"phone": "1", "text": "hi"}
    )
    assert not wa.needs_approval


async def test_completing_a_locate_stores_the_last_location(client, session, user):
    """Find-my-devices (FEATURES-50 #24): a phone.locate result lands on the device row."""
    from jarvis.api.routes.devices import complete_device_action
    from jarvis.db.models.ops import Device
    from jarvis.services.agent.executor import ToolExecutor

    phone = await _phone(session, user.id)
    r = await client.post(
        "/v1/auth/login", json={"email": "verbs@example.com", "password": PASSWORD}
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/v1/permissions/trust-devices", json={"days": 7}, headers=auth)

    proposal = await ToolGateway(session).propose(user.id, tool="phone.locate", args={})
    await session.commit()
    action = await ToolGateway(session).authorize_dispatch(proposal.action.id)
    await ToolExecutor(session).run(action)  # addresses it to the phone

    await complete_device_action(
        session, action, {"status": 200, "lat": 12.97, "lng": 77.59, "accuracy_m": 8}
    )
    await session.commit()

    stored = await session.get(Device, phone.id)
    assert stored.last_location["lat"] == 12.97 and stored.last_location["lng"] == 77.59
    assert stored.last_location["at"]


async def test_a_job_for_an_offline_phone_sends_a_wake_push(session, user, monkeypatch):
    """FCM-woken execution (FEATURES-50 #21): a job addressed to a phone that isn't
    connected nudges it with a push so the owner reopens the app to run it."""
    from jarvis.connectors import fcm
    from jarvis.core.config import get_settings
    from jarvis.db.models.ops import NotificationEndpoint
    from jarvis.services.agent.executor import ToolExecutor

    sent: list[tuple[str, str]] = []

    async def fake_send(self, address, *, title, body, task_id=None):  # noqa: ANN001
        sent.append((address, title))
        return {"ok": True}

    monkeypatch.setattr(fcm.FcmSender, "send", fake_send)
    monkeypatch.setattr(get_settings(), "fcm_credentials_path", "/x/fcm.json")

    await _phone(session, user.id)  # paired, but never connects a socket → offline
    session.add(
        NotificationEndpoint(user_id=user.id, channel="push", address="tok123", enabled=True)
    )
    await session.commit()

    proposal = await ToolGateway(session).propose(user.id, tool="phone.ring", args={})
    await session.commit()
    action = await ToolGateway(session).authorize_dispatch(proposal.action.id)
    await ToolExecutor(session).run(action)

    assert sent and sent[0][0] == "tok123"
