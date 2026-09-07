"""Attach an image to chat (FEATURES-50 #16)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105

# a 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000155a2b4ee0000000049454e44ae426082"
)


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("vis@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "vis@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_vision_endpoint_describes_the_image(client, auth, monkeypatch):
    import jarvis.services.vision as vision

    async def fake_describe(session, user_id, data, content_type, question=None, **kw):  # noqa: ANN001
        assert data == PNG and content_type == "image/png"
        return f"A tiny image. You asked: {question}"

    monkeypatch.setattr(vision, "describe", fake_describe)

    r = await client.post(
        "/v1/chat/vision",
        headers=auth,
        files={"file": ("shot.png", PNG, "image/png")},
        data={"question": "what is this?"},
    )
    assert r.status_code == 200
    assert "You asked: what is this?" in r.json()["text"]
