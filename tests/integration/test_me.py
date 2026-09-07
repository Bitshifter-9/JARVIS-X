"""Your data: export, audit, focus, wipe (FEATURES-50 44/45/9/8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.db.models.domain import Task
from jarvis.services.identity import IdentityService
from sqlalchemy import func, select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("me@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post("/v1/auth/login", json={"email": "me@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_export_returns_the_users_data_without_secrets(client, auth, session, user):
    session.add(Task(user_id=user.id, title="A task"))
    conv = Conversation(user_id=user.id, title="A chat")
    session.add(conv)
    await session.flush()
    session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user", content="hi"))
    await session.commit()

    r = await client.get("/v1/export", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert [t["title"] for t in body["tasks"]] == ["A task"]
    assert [c["title"] for c in body["conversations"]] == ["A chat"]
    assert body["messages"][0]["content"] == "hi"
    # No key material or embeddings.
    assert all("credentials" not in t for t in body["tasks"])
    assert all("embedding" not in m for m in body["memories"])


async def test_focus_picks_overdue_then_soonest(client, auth, session, user):
    now = datetime.now(UTC)
    session.add(Task(user_id=user.id, title="Late", due_at=now - timedelta(hours=3)))
    session.add(Task(user_id=user.id, title="Soon", due_at=now + timedelta(hours=1)))
    session.add(Task(user_id=user.id, title="Later", due_at=now + timedelta(days=2)))
    await session.commit()

    body = (await client.get("/v1/focus", headers=auth)).json()
    assert body["focus"]["title"] == "Late" and body["focus_reason"] == "overdue"
    assert body["counts"] == {"overdue": 1, "upcoming": 2}
    assert [t["title"] for t in body["upcoming"]] == ["Soon", "Later"]


async def test_audit_lists_and_filters(client, auth, session, user):
    # The wipe below writes an audit row; first check the trail reads.
    await client.post("/v1/account/wipe", params={"confirm": "DELETE"}, headers=auth)
    rows = (await client.get("/v1/audit", headers=auth)).json()
    assert any(r["action"] == "account.wiped" for r in rows)
    resp = await client.get("/v1/audit", params={"action": "account.wiped"}, headers=auth)
    filtered = resp.json()
    assert filtered and all(r["action"] == "account.wiped" for r in filtered)
    assert "account.wiped" in (await client.get("/v1/audit/actions", headers=auth)).json()


async def test_wipe_needs_confirmation_and_clears_content(client, auth, session, user):
    session.add(Task(user_id=user.id, title="Doomed"))
    await session.commit()

    refused = await client.post("/v1/account/wipe", headers=auth)
    assert refused.status_code == 409  # no confirm

    ok = await client.post("/v1/account/wipe", params={"confirm": "DELETE"}, headers=auth)
    assert ok.status_code == 200 and ok.json()["wiped"]["tasks"] >= 1
    remaining = await session.scalar(
        select(func.count()).select_from(Task).where(Task.user_id == user.id)
    )
    assert remaining == 0
    # The account still exists — you can keep using it.
    assert (await client.get("/v1/auth/me", headers=auth)).status_code == 200


async def test_weekly_review_summarises_the_last_seven_days(client, auth, session, user):
    from datetime import timedelta

    from jarvis.db.models.domain import WorkSession

    now = datetime.now(UTC)
    done = Task(
        user_id=user.id, title="Shipped it", status="done",
        completed_at=now - timedelta(days=2),
    )
    slip = Task(user_id=user.id, title="Missed it", due_at=now - timedelta(days=1))
    soon = Task(user_id=user.id, title="Next week", due_at=now + timedelta(days=3))
    session.add_all([done, slip, soon])
    session.add(
        WorkSession(user_id=user.id, started_at=now - timedelta(days=1), active_minutes=90)
    )
    await session.commit()

    r = await client.get("/v1/review/weekly", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["done"] == 1 and body["done_titles"] == ["Shipped it"]
    assert body["slipped"] == 1 and body["slipped_titles"] == ["Missed it"]
    assert body["focus_hours"] == 1.5
    assert [u["title"] for u in body["upcoming"]] == ["Next week"]
