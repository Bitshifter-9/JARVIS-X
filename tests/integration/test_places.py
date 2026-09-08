"""Location trails + place learning (#5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.identity import IdentityService
from jarvis.services.places import places, record_locations

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("loc@example.com", PASSWORD)
    await session.commit()
    return u


async def test_fixes_are_rounded_and_places_labelled(session, user):
    # Nights at ~home (12.9716, 77.5946), weekday days at ~work (12.9081, 77.6476).
    now = datetime(2026, 9, 7, tzinfo=UTC)  # a Monday
    home = {"lat": 12.9716, "lng": 77.5946}
    work = {"lat": 12.9081, "lng": 77.6476}
    samples = []
    for d in range(10):
        base = now - timedelta(days=d)
        samples.append({**home, "at": base.replace(hour=23).isoformat()})   # night → home
        samples.append({**work, "at": base.replace(hour=11).isoformat()})   # weekday day → work
    stored = await record_locations(session, user.id, samples)
    assert stored == 20
    await session.flush()

    result = await places(session, user.id, tz="UTC")
    labels = {p["label"] for p in result}
    assert "home" in labels
    assert "work" in labels
    # Coordinates are coarse (rounded to the grid), never the exact fix.
    assert all(abs(p["lat"] * 200 - round(p["lat"] * 200)) < 1e-6 for p in result)


async def test_endpoint_requires_a_paired_device(client, session):
    await IdentityService(session).register("lo@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "lo@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # No device paired → forbidden; and /v1/places is empty.
    bad = await client.post("/v1/devices/00000000-0000-0000-0000-000000000000/location",
                            headers=auth, json=[{"lat": 1.0, "lng": 2.0}])
    assert bad.status_code == 403
    assert (await client.get("/v1/places", headers=auth)).json() == []
