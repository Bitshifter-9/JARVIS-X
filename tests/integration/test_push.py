"""Phase 2.8: push is the first rung, and the phone registers itself."""

from __future__ import annotations

import pytest
from jarvis.connectors.fcm import FcmSender, message_payload
from jarvis.services.notification import Channel

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client):
    await client.post("/v1/auth/register", json={"email": "push@example.com", "password": PASSWORD})
    tokens = (
        await client.post(
            "/v1/auth/login", json={"email": "push@example.com", "password": PASSWORD}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_the_phone_registers_its_token_once_however_often_it_launches(client, auth):
    body = {"channel": "push", "address": "fcm-token-abc123456"}
    first = await client.put("/v1/notifications/endpoints", headers=auth, json=body)
    second = await client.put("/v1/notifications/endpoints", headers=auth, json=body)
    assert first.status_code == 200 and second.json()["id"] == first.json()["id"]

    listed = (await client.get("/v1/notifications/endpoints", headers=auth)).json()
    assert len(listed) == 1
    assert listed[0]["escalation_rank"] == 0, "push is the first rung"
    assert listed[0]["address"].endswith("c123456") and "fcm-token" not in listed[0]["address"]


async def test_channels_rank_in_ladder_order_and_unknown_ones_are_refused(client, auth):
    call = (
        await client.put(
            "/v1/notifications/endpoints",
            headers=auth,
            json={"channel": "call", "address": "+911234"},
        )
    ).json()
    telegram = (
        await client.put(
            "/v1/notifications/endpoints",
            headers=auth,
            json={"channel": "telegram", "address": "55"},
        )
    ).json()
    assert telegram["escalation_rank"] < call["escalation_rank"]
    bad = await client.put(
        "/v1/notifications/endpoints", headers=auth, json={"channel": "pigeon", "address": "x"}
    )
    assert bad.status_code == 409

    gone = await client.delete(f"/v1/notifications/endpoints/{call['id']}", headers=auth)
    assert gone.json() == {"deleted": True}
    assert len((await client.get("/v1/notifications/endpoints", headers=auth)).json()) == 1


async def test_an_endpoint_belongs_to_its_owner(client, auth, session):
    from jarvis.services.identity import IdentityService

    await IdentityService(session).register("other@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD}
    )
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    mine = (
        await client.put(
            "/v1/notifications/endpoints", headers=auth, json={"channel": "push", "address": "tok"}
        )
    ).json()
    assert (
        await client.delete(f"/v1/notifications/endpoints/{mine['id']}", headers=other)
    ).status_code == 404


def test_the_fcm_body_reaches_the_app_in_every_state():
    payload = message_payload("tok", title="Report", body="Due in 2 hours", task_id="t1")
    message = payload["message"]
    assert message["token"] == "tok"
    assert message["notification"] == {"title": "Report", "body": "Due in 2 hours"}
    assert message["data"]["task_id"] == "t1"
    assert message["android"]["notification"]["channel_id"] == "jarvis_alerts"
    # A deadline routes to Goals by default.
    assert message["data"]["route"] == "goals"


def test_the_payload_deep_links_by_kind():
    # An approval carries its id and routes to the Approvals tab (interactive notifications).
    p = message_payload("tok", title="Approval needed", body="Email your prof",
                        data={"kind": "approval", "id": "a1"})
    data = p["message"]["data"]
    assert data["kind"] == "approval" and data["route"] == "approvals" and data["id"] == "a1"
    # Every data value is a string, as FCM requires.
    assert all(isinstance(v, str) for v in data.values())


async def test_a_rejected_push_falls_through_to_the_next_rung():
    """FCM says the token is dead → the sender raises → the ladder moves on."""
    sent: list[dict] = []

    async def transport(payload):  # noqa: ANN001, ANN202
        sent.append(payload)
        return 404

    sender = FcmSender("{}", "proj", transport=transport)
    with pytest.raises(RuntimeError, match="404"):
        await sender.send("dead-token", title="t", body="b")
    assert sent and sent[0]["message"]["token"] == "dead-token"


def test_the_worker_offers_push_only_when_credentials_exist(monkeypatch):
    from jarvis.core.config import get_settings
    from jarvis.workers.notify import build_senders

    monkeypatch.setattr(get_settings(), "fcm_credentials_path", "")
    assert Channel.PUSH not in build_senders(None)
    monkeypatch.setattr(get_settings(), "fcm_credentials_path", '{"project_id": "p"}')
    assert Channel.PUSH in build_senders(None)
