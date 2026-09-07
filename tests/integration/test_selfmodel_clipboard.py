"""Self-model export (#50) and clipboard history (#7)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("sm@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "sm@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_clipboard_history_accumulates_and_searches(client, auth):
    for t in ("first copy", "second copy about Kubernetes", "third copy"):
        await client.post("/v1/clipboard", headers=auth, json={"text": t, "device": "mac"})
    # A duplicate of the newest is not re-added.
    await client.post("/v1/clipboard", headers=auth, json={"text": "third copy", "device": "mac"})

    hist = (await client.get("/v1/clipboard/history", headers=auth)).json()
    assert [h["text"] for h in hist] == ["third copy", "second copy about Kubernetes", "first copy"]

    found = (await client.get("/v1/clipboard/history?q=kubernetes", headers=auth)).json()
    assert len(found) == 1 and "Kubernetes" in found[0]["text"]


async def test_self_model_bundle_shape(client, auth):
    bundle = (await client.get("/v1/self-model", headers=auth)).json()
    assert bundle["version"]
    assert "persona" in bundle["model"]
    assert "phrasebook" in bundle["model"]
    assert "rhythm" in bundle["model"]
    assert "counts" in bundle
