"""Natural-language quick-add (PLAN.md 13.6): a typed line becomes a dated task, no
model needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.identity import IdentityService


@pytest.fixture
async def auth(client, session):
    await IdentityService(session).register("q@example.com", "correct-horse-battery")
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "q@example.com", "password": "correct-horse-battery"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_quick_add_reads_a_relative_deadline(client, auth):
    r = await client.post("/v1/tasks/quick", json={"text": "pay rent friday 6pm"}, headers=auth)
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["due_at"] is not None
    due = datetime.fromisoformat(task["due_at"])
    assert due > datetime.now(UTC)
    assert due < datetime.now(UTC) + timedelta(days=8)
    assert task["evidence_span"] == "pay rent friday 6pm"


async def test_quick_add_without_a_date_is_still_a_task(client, auth):
    r = await client.post("/v1/tasks/quick", json={"text": "buy milk"}, headers=auth)
    assert r.status_code == 201
    assert r.json()["title"] == "buy milk"
    assert r.json()["due_at"] is None
