"""The notification mirror (PLAN.md 10.4.4): titles from a paired phone become untrusted
events, a matching routine fires, and a stranger's device is refused."""

from __future__ import annotations

import pytest
from jarvis.db.models.job import Job
from jarvis.db.models.source import Event, SourceObject
from jarvis.services.device import DeviceService, generate_keypair, sign
from jarvis.services.identity import IdentityService
from jarvis.services.routines import RoutineService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("mirror@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "mirror@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _pair(session, user_id, name="Pixel"):
    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user_id, name=name, platform="android", public_key_pem=public_pem
    )
    await session.commit()
    device = await devices.complete_pairing(
        user_id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


@pytest.fixture
async def phone(session, user):
    return await _pair(session, user.id)


async def test_titles_become_events_and_trigger_routines(client, auth, session, user, phone):
    await RoutineService(session).create(
        user.id,
        name="Amma pinged",
        prompt="Tell me what Amma wants, briefly",
        trigger={"provider": "phone", "from": "whatsapp", "subject": "amma"},
    )
    await session.commit()

    items = [
        {"package": "com.whatsapp", "app": "WhatsApp", "title": "Amma", "text": "", "key": "w:1"},
        {"package": "com.google.android.gm", "app": "Gmail", "title": "Rao: Viva", "key": "g:1"},
    ]
    r = await client.post(f"/v1/devices/{phone.id}/notifications", json=items, headers=auth)
    assert r.status_code == 202, r.text
    assert r.json() == {"received": 2, "new": 2}

    events = (await session.scalars(select(Event).where(Event.provider == "phone"))).all()
    assert len(events) == 2 and all(e.trust == "untrusted" for e in events)
    assert {e.payload["author"] for e in events} == {"WhatsApp", "Gmail"}

    # The WhatsApp one matched the routine; the Gmail one did not.
    runs = (await session.scalars(select(Job).where(Job.kind == "agent.run"))).all()
    assert len(runs) == 1
    assert "Amma" in runs[0].payload["context"]

    # Redelivery is idempotent by key.
    again = await client.post(f"/v1/devices/{phone.id}/notifications", json=items, headers=auth)
    assert again.json() == {"received": 2, "new": 0}


async def test_a_title_only_forward_carries_no_body(client, auth, session, user, phone):
    items = [{"package": "com.whatsapp", "app": "WhatsApp", "title": "Amma", "text": ""}]
    await client.post(f"/v1/devices/{phone.id}/notifications", json=items, headers=auth)
    event = await session.scalar(select(Event).where(Event.provider == "phone"))
    assert event.payload["text"] == "Amma"  # the title stands in; no body was sent
    assert await session.scalar(select(SourceObject)) is None  # nothing stored until normalize


async def test_a_strangers_device_is_refused(client, auth, session):
    other = await IdentityService(session).register("other@example.com", PASSWORD)
    await session.commit()
    theirs = await _pair(session, other.id, name="Their phone")
    r = await client.post(
        f"/v1/devices/{theirs.id}/notifications",
        json=[{"package": "com.whatsapp", "title": "hi"}],
        headers=auth,
    )
    assert r.status_code == 403
