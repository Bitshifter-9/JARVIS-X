"""Screen memory ingestion (#1) and personalised micro-lessons (#35)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class _Resp:
    def __init__(self, text): self.text = text  # noqa: E704


class _Router:
    async def chat(self, messages, *, user_id=None, **kwargs):  # noqa: ANN001
        return _Resp("• Point one\n• Point two\nTry: do the thing.")


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("sl@example.com", PASSWORD)
    await session.commit()
    return u


async def test_micro_lesson_from_a_topic(session, user):
    from jarvis.services.micro_lessons import micro_lesson

    out = await micro_lesson(session, user.id, topic="Kubernetes", router=_Router())
    assert out["topic"] == "Kubernetes"
    assert "Point one" in out["lesson"]


async def test_micro_lesson_empty_without_gaps(session, user):
    from jarvis.services.micro_lessons import micro_lesson

    out = await micro_lesson(session, user.id, router=_Router())  # no topic, no gaps
    assert out == {}


async def test_screen_ingest_is_searchable(client, session):
    await IdentityService(session).register("sc@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "sc@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.models.ops import Device
    from jarvis.db.session import get_sessionmaker
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "sc@example.com"))).one()
        dev = Device(user_id=uid, name="Mac", platform="macos", public_key_pem="-",
                     fingerprint=uuid.uuid4().hex, paired_at=datetime.now(UTC))
        s.add(dev)
        await s.commit()
        did = str(dev.id)

    body = [{"app": "Safari", "title": "Kubernetes docs",
             "text": "Kubernetes ingress with nginx controller notes"}]
    posted = (await client.post(f"/v1/devices/{did}/screen", headers=auth, json=body)).json()
    assert posted["stored"] == 1
    # Re-posting the identical screen doesn't duplicate.
    (await client.post(f"/v1/devices/{did}/screen", headers=auth, json=body))

    found = (await client.get("/v1/search?q=nginx", headers=auth)).json()["results"]
    assert any("Kubernetes" in (r.get("title") or "") for r in found)
