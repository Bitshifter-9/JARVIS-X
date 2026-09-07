"""Recurring deadlines and pinned conversations (FEATURES-50 3, 13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import Task
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("rec@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post("/v1/auth/login", json={"email": "rec@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_completing_a_recurring_deadline_spawns_the_next(session, user):
    goals = GoalService(session)
    monday = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)  # a Monday
    task = await goals.create_task(
        user.id, title="Standup", due_at=monday, timezone="UTC", recurrence="weekdays"
    )
    await session.commit()

    await goals.update_task(user.id, task.id, status="done")
    await session.commit()
    tasks = (await session.scalars(select(Task).order_by(Task.due_at))).all()
    assert len(tasks) == 2
    nxt = tasks[1]
    assert nxt.status == "open" and nxt.recurrence == "weekdays"
    assert nxt.due_at == monday + timedelta(days=1)  # Tuesday
    # And that one, done on Friday, skips the weekend.
    friday = await goals.create_task(
        user.id, title="Standup", due_at=datetime(2026, 9, 11, 9, tzinfo=UTC),
        timezone="UTC", recurrence="weekdays",
    )
    await goals.update_task(user.id, friday.id, status="done")
    mon14 = datetime(2026, 9, 14, 9, tzinfo=UTC)
    monday_next = await session.scalar(
        select(Task).where(Task.title == "Standup", Task.due_at == mon14)
    )
    assert monday_next is not None  # jumped over Sat/Sun


async def test_a_non_recurring_task_spawns_nothing(session, user):
    goals = GoalService(session)
    t = await goals.create_task(
        user.id, title="One-off", due_at=datetime.now(UTC) + timedelta(days=1)
    )
    await goals.update_task(user.id, t.id, status="done")
    assert len((await session.scalars(select(Task))).all()) == 1


async def test_pinned_conversations_sort_first(client, auth):
    a = (await client.post("/v1/conversations", headers=auth)).json()
    b = (await client.post("/v1/conversations", headers=auth)).json()
    # b is newer, so it leads by default...
    listed = await client.get("/v1/conversations", headers=auth)
    assert listed.json()[0]["id"] == b["id"]
    # ...pin a, and it jumps to the top.
    pinned = await client.patch(f"/v1/conversations/{a['id']}", json={"pinned": True}, headers=auth)
    assert pinned.json()["pinned"] is True
    listed = await client.get("/v1/conversations", headers=auth)
    assert listed.json()[0]["id"] == a["id"]
    # unpin
    await client.patch(f"/v1/conversations/{a['id']}", json={"pinned": False}, headers=auth)
    assert (await client.get("/v1/conversations", headers=auth)).json()[0]["id"] == b["id"]
