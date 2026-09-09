"""Device presence and notification identity — the two failures behind "JARVIS only works
when the app is open" and "the same phone notification appears several times"."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.ops import Device
from jarvis.services.device.service import PRESENCE_TIMEOUT, DeviceService
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def paired(session):
    user = await IdentityService(session).register("presence@example.com", PASSWORD)
    device = Device(
        user_id=user.id, name="Pixel", platform="android", public_key_pem="-",
        fingerprint=uuid.uuid4().hex, paired_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    session.add(device)
    await session.commit()
    return user, device


# ── presence ────────────────────────────────────────────────────────────
async def test_presence_follows_the_heartbeat_not_an_open_socket(session, paired):
    _, device = paired
    devices = DeviceService(session)

    # A fresh beat is online — with no connection row at all, because the background
    # runtime need not hold a socket.
    assert await devices.is_online(device.id) is True

    # Gone quiet past the timeout is offline, even though nothing "disconnected".
    device.last_seen_at = datetime.now(UTC) - PRESENCE_TIMEOUT - timedelta(seconds=5)
    await session.flush()
    assert await devices.is_online(device.id) is False

    # A beat brings it back without any socket lifecycle.
    await devices.touch(device.id)
    await session.flush()
    assert await devices.is_online(device.id) is True


async def test_a_revoked_device_is_never_online(session, paired):
    _, device = paired
    device.revoked_at = datetime.now(UTC)
    await session.flush()
    assert await DeviceService(session).is_online(device.id) is False


# ── notification identity ───────────────────────────────────────────────
@pytest.fixture
async def auth_device(client, session):
    await IdentityService(session).register("mirror@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "mirror@example.com", "password": PASSWORD}
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.session import get_sessionmaker
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "mirror@example.com"))).one()
        dev = Device(user_id=uid, name="Pixel", platform="android", public_key_pem="-",
                     fingerprint=uuid.uuid4().hex, paired_at=datetime.now(UTC))
        s.add(dev)
        await s.commit()
        return auth, str(dev.id), uid


def _notif(key: str, text: str, at: str) -> dict:
    return {"package": "com.whatsapp", "app": "WhatsApp", "title": "Amma",
            "text": text, "at": at, "key": key}


async def test_the_same_notification_reposted_is_one_event(client, auth_device):
    """Android re-posts a notification constantly and updates a chat in place. The old key
    mixed in postTime, so every repeat looked new — this is the duplicate the user saw."""
    auth, did, _ = auth_device
    slot = "0|com.whatsapp|1|null|10123"  # Android's own sbn.key: the notification slot

    first = (await client.post(f"/v1/devices/{did}/notifications", headers=auth,
                               json=[_notif(slot, "are you free?", "2026-09-09T10:00:00+00:00")])).json()
    assert first["new"] == 1

    # Same slot, same content, later timestamp — a repost, not a new message.
    again = (await client.post(f"/v1/devices/{did}/notifications", headers=auth,
                               json=[_notif(slot, "are you free?", "2026-09-09T10:05:00+00:00")])).json()
    assert again["new"] == 0, "a repost of the same notification must not become a second event"


async def test_a_real_new_message_in_the_same_chat_still_gets_through(client, auth_device):
    auth, did, _ = auth_device
    slot = "0|com.whatsapp|2|null|10123"
    a = (await client.post(f"/v1/devices/{did}/notifications", headers=auth,
                           json=[_notif(slot, "are you free?", "2026-09-09T10:00:00+00:00")])).json()
    b = (await client.post(f"/v1/devices/{did}/notifications", headers=auth,
                           json=[_notif(slot, "call me when you can", "2026-09-09T10:06:00+00:00")])).json()
    assert a["new"] == 1 and b["new"] == 1


async def test_forwarding_a_notification_counts_as_a_heartbeat(client, auth_device, session):
    """A phone mirroring notifications is plainly alive; presence must not need a socket."""
    auth, did, _ = auth_device
    from jarvis.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        dev = await s.get(Device, uuid.UUID(did))
        dev.last_seen_at = datetime.now(UTC) - PRESENCE_TIMEOUT - timedelta(minutes=5)
        await s.commit()
        assert await DeviceService(s).is_online(dev.id) is False

    await client.post(f"/v1/devices/{did}/notifications", headers=auth,
                      json=[_notif("0|com.whatsapp|9|null|10123", "hi", "2026-09-09T11:00:00+00:00")])

    async with get_sessionmaker()() as s:
        assert await DeviceService(s).is_online(uuid.UUID(did)) is True
