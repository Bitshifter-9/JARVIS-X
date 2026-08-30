"""Phase 1.6 gate: Telegram alerts and inline approve/reject.

Exit test from PLAN.md §12: *approve from Telegram; the run resumes; evidence returns.*
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.connectors.telegram.client import RecordingTransport
from jarvis.connectors.telegram.service import TelegramService
from jarvis.db.models.agent import ActionStatus
from jarvis.db.models.ops import AuditLog, Schedule
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
CHAT_ID = "5551234"
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}


@pytest.fixture
def transport():
    return RecordingTransport()


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("tg@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def telegram(session, transport, user):
    service = TelegramService(session, transport)
    await service.link_chat(user.id, CHAT_ID)
    await session.commit()
    return service


def _callback(data: str, chat_id: str = CHAT_ID) -> dict:
    return {
        "callback_query": {
            "id": "cbq-1",
            "data": data,
            "message": {"chat": {"id": chat_id}},
        }
    }


# ── identity ───────────────────────────────────────────────────────────
async def test_a_chat_id_is_not_an_account_until_it_is_linked(session, transport):
    """Every surface is untrusted until its identity maps to a user (blueprint §2)."""
    service = TelegramService(session, transport)
    assert await service.user_for_chat("9999") is None

    outcome = await service.handle_update(_callback("approve:" + str(uuid.uuid4()), "9999"))
    assert outcome.handled is False
    assert "not linked" in transport.calls[0][1]["text"]


async def test_a_linked_chat_resolves_to_its_owner(session, telegram, user):
    assert await telegram.user_for_chat(CHAT_ID) == user.id


# ── the gate: approve from Telegram ────────────────────────────────────
async def test_approving_from_telegram_unblocks_dispatch(session, telegram, user, transport):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    await telegram.send_approval_card(CHAT_ID, approval=proposal.approval, action=proposal.action)
    outcome = await telegram.handle_update(_callback(f"approve:{proposal.approval.id}"))
    await session.commit()

    assert outcome.handled is True
    assert outcome.approval_id == proposal.approval.id

    # The action can now dispatch — the whole point of the round trip.
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    assert action.status == ActionStatus.DISPATCHED.value


async def test_rejecting_from_telegram_keeps_dispatch_blocked(session, telegram, user):
    from jarvis.core.errors import PolicyDenied

    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    await telegram.handle_update(_callback(f"reject:{proposal.approval.id}"))
    await session.commit()

    with pytest.raises(PolicyDenied):
        await gateway.authorize_dispatch(proposal.action.id)


async def test_another_account_cannot_decide_your_approval(session, transport, user):
    """A replayed callback_data from someone else's card must find nothing."""
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    intruder = await IdentityService(session).register("intruder@example.com", PASSWORD)
    service = TelegramService(session, transport)
    await service.link_chat(intruder.id, "7770000")
    await session.commit()

    outcome = await service.handle_update(
        _callback(f"approve:{proposal.approval.id}", "7770000")
    )
    assert outcome.handled is False
    assert proposal.approval.decision is None


async def test_deciding_twice_is_reported_not_crashed(session, telegram, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    first = await telegram.handle_update(_callback(f"approve:{proposal.approval.id}"))
    await session.commit()
    second = await telegram.handle_update(_callback(f"approve:{proposal.approval.id}"))

    assert first.handled is True
    assert second.handled is False


async def test_a_malformed_callback_id_is_handled(session, telegram):
    outcome = await telegram.handle_update(_callback("approve:not-a-uuid"))
    assert outcome.handled is False


async def test_decisions_are_audited_with_their_channel(session, telegram, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await telegram.handle_update(_callback(f"approve:{proposal.approval.id}"))
    await session.commit()

    entries = (await session.scalars(select(AuditLog))).all()
    telegram_entries = [e for e in entries if e.actor == "telegram"]
    assert telegram_entries
    assert telegram_entries[0].detail["chat_id"] == CHAT_ID


# ── acknowledgement from an alert ──────────────────────────────────────
async def test_acknowledging_from_telegram_cancels_later_alerts(session, telegram, user):
    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Submit", due_at=datetime.now(UTC) + timedelta(days=2)
    )
    await session.commit()

    outcome = await telegram.handle_update(_callback(f"ack:{task.id}"))
    await session.commit()

    assert outcome.handled is True
    pending = (
        await session.scalars(
            select(Schedule).where(Schedule.task_id == task.id, Schedule.status == "pending")
        )
    ).all()
    assert pending == []


# ── outbound formatting ────────────────────────────────────────────────
async def test_an_approval_card_states_the_recipients(session, telegram, user, transport):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()

    await telegram.send_approval_card(CHAT_ID, approval=proposal.approval, action=proposal.action)
    method, payload = transport.calls[-1]

    assert method == "sendMessage"
    assert "@team" in payload["text"]
    assert "R2" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"].split(":")[0] for b in buttons] == ["approve", "reject"]


async def test_an_r3_card_warns_that_the_mac_must_confirm_too(session, telegram, user, transport):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="mac.run_template",
        args={"template": "git.pull", "params": {"path": "/Users/p/proj"}},
    )
    await session.commit()

    await telegram.send_approval_card(CHAT_ID, approval=proposal.approval, action=proposal.action)
    assert "confirmation on your Mac" in transport.calls[-1][1]["text"]


async def test_a_deadline_alert_shows_time_left_and_probability(session, telegram, transport):
    await telegram.send_deadline_alert(
        CHAT_ID,
        task_title="CS401 Assignment 3",
        due_at=datetime.now(UTC) + timedelta(hours=2, minutes=30),
        kind="T-2h",
        probability=0.34,
        task_id=uuid.uuid4(),
    )
    text = transport.calls[-1][1]["text"]
    assert "2h 29m" in text or "2h 30m" in text
    assert "34%" in text


async def test_markdown_specials_are_escaped(session, telegram, transport):
    """An unescaped character means a 400 from Telegram, which silently drops the alert
    that most needed to arrive."""
    await telegram.send_deadline_alert(
        CHAT_ID,
        task_title="Report_v2 (final!) [draft]",
        due_at=datetime.now(UTC) + timedelta(hours=1),
        kind="T-1h",
    )
    text = transport.calls[-1][1]["text"]
    assert r"Report\_v2" in text
    assert r"\(final\!\)" in text
    assert r"\[draft\]" in text


async def test_free_text_is_not_a_command_channel(session, telegram):
    """Only inline buttons act. Arbitrary text would need interpreting, and interpreting
    untrusted input is what the policy boundary keeps away from effects."""
    outcome = await telegram.handle_update(
        {"message": {"chat": {"id": CHAT_ID}, "text": "delete everything"}}
    )
    assert outcome.handled is False
