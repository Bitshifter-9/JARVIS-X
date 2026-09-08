"""Photo index (#8) and energy/health correlation (#38)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth_device(client, session):
    await IdentityService(session).register("ph@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "ph@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    from jarvis.db.models.identity import User
    from jarvis.db.models.ops import Device
    from jarvis.db.session import get_sessionmaker
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "ph@example.com"))).one()
        dev = Device(user_id=uid, name="Phone", platform="android", public_key_pem="-",
                     fingerprint=uuid.uuid4().hex, paired_at=datetime.now(UTC))
        s.add(dev)
        await s.commit()
        return auth, str(dev.id), uid


async def test_photo_ocr_is_searchable(client, auth_device):
    auth, did, _ = auth_device
    body = [{"caption": "a receipt", "text": "Taj Hotel Goa total 4200 INR"}]
    posted = (await client.post(f"/v1/devices/{did}/photo", headers=auth, json=body)).json()
    assert posted["stored"] == 1
    found = (await client.get("/v1/search?q=Goa", headers=auth)).json()["results"]
    assert any("receipt" in (r.get("title") or "") for r in found)


async def test_health_correlation(session, auth_device):
    auth, did, uid = auth_device
    from jarvis.db.models.domain import WorkSession
    from jarvis.db.session import get_sessionmaker
    from jarvis.services.health_metrics import correlation, record_health

    async with get_sessionmaker()() as s:
        now = datetime.now(UTC)
        # More sleep on days with more focus → positive correlation.
        samples = []
        for d in range(8):
            base = now - timedelta(days=d)
            sleep = 360 + d * 20
            samples.append({"day": base.isoformat(), "sleep_minutes": sleep, "steps": 5000})
            s.add(WorkSession(user_id=uid, task_id=None, started_at=base,
                              active_minutes=sleep - 300, source="focus"))
        await record_health(s, uid, samples)
        await s.commit()

        out = await correlation(s, uid, tz="UTC")
        assert out["enough_data"] is True
        sleep_corr = next(c for c in out["correlations"] if c["metric"] == "sleep")
        assert sleep_corr["correlation"] > 0.3
