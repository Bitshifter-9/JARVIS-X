"""Ambient audio (#2) and meeting capture (#10): transcript ingest + action items."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth_and_device(client, session):
    await IdentityService(session).register("tr@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "tr@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.models.ops import Device
    from jarvis.db.session import get_sessionmaker
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "tr@example.com"))).one()
        dev = Device(user_id=uid, name="Mac", platform="macos", public_key_pem="-",
                     fingerprint=uuid.uuid4().hex, paired_at=datetime.now(UTC))
        s.add(dev)
        await s.commit()
        return auth, str(dev.id)


async def test_ambient_transcript_is_searchable(client, auth_and_device):
    auth, did = auth_and_device
    body = {"text": "reminder about the Kubernetes rollout plan for next quarter",
            "kind": "ambient"}
    posted = (await client.post(f"/v1/devices/{did}/transcript", headers=auth, json=body)).json()
    assert posted["stored"] == 1
    found = (await client.get("/v1/search?q=Kubernetes", headers=auth)).json()["results"]
    assert any("Overheard" in (r.get("title") or "") for r in found)


async def test_meeting_extracts_action_items(client, auth_and_device):
    auth, did = auth_and_device
    body = {"text": "Good sync. I'll send the budget by Friday. Priya will review it.",
            "kind": "meeting", "title": "Budget sync"}
    posted = (await client.post(f"/v1/devices/{did}/transcript", headers=auth, json=body)).json()
    assert posted["tasks_created"] >= 1
    tasks = (await client.get("/v1/tasks", headers=auth)).json()
    assert any("budget" in t["title"].lower() for t in tasks)
