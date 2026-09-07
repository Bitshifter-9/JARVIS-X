"""Disliked reminders: mute a sender/channel, suppress its future deadlines (#14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import ReminderMute, Task
from jarvis.db.models.source import SourceObject
from jarvis.services import reminders
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_signature_normalizes_sender():
    assert reminders.signature_for("gmail", "Promo <promo@brand.com>") == "gmail:promo@brand.com"
    assert reminders.signature_for("whatsapp", "News Channel") == "whatsapp:news channel"
    assert reminders.signature_for("gmail", None) is None
    assert reminders.signature_for(None, "x") is None


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("dislike@example.com", PASSWORD)
    await session.commit()
    return u


async def _mail_task(session, user_id, oid, sender, title):
    src = SourceObject(user_id=user_id, provider="gmail", object_id=oid, kind="email",
                       title=title, author=sender, occurred_at=datetime.now(UTC))
    session.add(src)
    await session.flush()
    task = await GoalService(session).create_task(
        user_id, title=title, due_at=datetime.now(UTC) + timedelta(days=1),
        timezone="UTC", source_id=src.id,
    )
    return task


async def test_dislike_mutes_sender_and_clears_its_open_tasks(session, user):
    t1 = await _mail_task(session, user.id, "m1", "Promo <promo@brand.com>", "Sale ends soon")
    t2 = await _mail_task(session, user.id, "m2", "PROMO <promo@brand.com>", "Another sale")
    keep = await _mail_task(session, user.id, "m3", "boss@work.com", "Report due")
    await session.flush()

    result = await reminders.dislike_task(session, user.id, t1.id)
    assert result["muted"] is True
    assert result["dismissed"] == 2  # both promo tasks, one dislike

    await session.refresh(t1)
    await session.refresh(t2)
    await session.refresh(keep)
    assert t1.status == "cancelled" and t2.status == "cancelled"
    assert keep.status == "open"  # a different sender is untouched

    # The sender is now muted, and future deadlines from it are suppressed.
    assert await reminders.is_muted(session, user.id, "gmail", "promo@brand.com") is True
    assert await reminders.is_muted(session, user.id, "gmail", "boss@work.com") is False


async def test_mute_list_and_unmute(session, user):
    t = await _mail_task(session, user.id, "m1", "spam@x.com", "buy now")
    await reminders.dislike_task(session, user.id, t.id)

    mutes = await reminders.list_mutes(session, user.id)
    assert len(mutes) == 1 and "spam@x.com" in mutes[0]["label"]

    mute_id = (await session.scalars(
        select(ReminderMute.id).where(ReminderMute.user_id == user.id)
    )).one()
    assert await reminders.unmute(session, user.id, mute_id) is True
    assert await reminders.list_mutes(session, user.id) == []
    assert await reminders.is_muted(session, user.id, "gmail", "spam@x.com") is False


async def test_dislike_without_source_just_dismisses(session, user):
    task = await GoalService(session).create_task(
        user.id, title="manual", due_at=datetime.now(UTC) + timedelta(days=1), timezone="UTC",
    )
    result = await reminders.dislike_task(session, user.id, task.id)
    assert result["muted"] is False and result["dismissed"] == 1
    await session.refresh(task)
    assert task.status == "cancelled"
    assert await reminders.list_mutes(session, user.id) == []


async def test_endpoints(client, session):
    await IdentityService(session).register("rr@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "rr@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "rr@example.com"))).one()
        t = await _mail_task(s, uid, "m1", "junk@x.com", "spam deadline")
        await s.commit()
        tid = str(t.id)

    disliked = (await client.post(f"/v1/reminders/dislike/{tid}", headers=auth)).json()
    assert disliked["muted"] is True

    mutes = (await client.get("/v1/reminders/mutes", headers=auth)).json()
    assert len(mutes) == 1
    # The task is gone from the open list.
    async with get_sessionmaker()() as s:
        row = await s.get(Task, __import__("uuid").UUID(tid))
        assert row.status == "cancelled"

    assert (await client.delete(f"/v1/reminders/mutes/{mutes[0]['id']}", headers=auth)).json()["ok"]
    assert (await client.get("/v1/reminders/mutes", headers=auth)).json() == []


async def _phone_task(session, user_id, oid, app, chat):
    # A mirrored phone notification: provider "phone", author = the app label (same for every
    # chat), the chat/group name in the title. This is how WhatsApp reminders arrive.
    src = SourceObject(user_id=user_id, provider="phone", object_id=oid, kind="notification",
                       title=chat, author=app, occurred_at=datetime.now(UTC))
    session.add(src)
    await session.flush()
    return await GoalService(session).create_task(
        user_id, title=f"{chat} deadline", due_at=datetime.now(UTC) + timedelta(days=1),
        timezone="UTC", source_id=src.id,
    )


async def test_muting_one_whatsapp_chat_spares_the_rest(session, user):
    fam = await _phone_task(session, user.id, "w1", "WhatsApp", "Family Group")
    work = await _phone_task(session, user.id, "w2", "WhatsApp", "Work Group")
    await session.flush()

    result = await reminders.dislike_task(session, user.id, fam.id)
    assert result["muted"] is True
    assert result["signature"] == "phone:whatsapp:family group"
    assert result["dismissed"] == 1  # only the Family Group task, not all WhatsApp

    await session.refresh(fam)
    await session.refresh(work)
    assert fam.status == "cancelled"
    assert work.status == "open"  # a different WhatsApp chat is untouched

    # Future Family Group messages are muted; Work Group and other WhatsApp chats are not.
    assert await reminders.is_muted(session, user.id, "phone", "WhatsApp", "Family Group") is True
    assert await reminders.is_muted(session, user.id, "phone", "WhatsApp", "Work Group") is False
    assert await reminders.is_muted(session, user.id, "phone", "WhatsApp", "Random") is False
