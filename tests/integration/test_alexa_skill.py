"""Phase 4 gates: the skill answers only Amazon, only for our skill id, and only for a
linked account — and every intent runs through the same services the app uses.

Exit tests from PLAN.md §12: *the simulator resolves each intent*, *only our skill id is
accepted*, *link, call an authorized intent, unlink*.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.connectors.alexa.skill import handle_request
from jarvis.core.config import get_settings
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SKILL_ID = "amzn1.ask.skill.jarvis-x-test"


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("alexa@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture(autouse=True)
def _no_signature_in_tests(monkeypatch):
    """A live cert chain from Amazon is not available in a test; the verifier itself is
    covered in ``tests/unit/test_alexa.py``."""
    settings = get_settings()
    monkeypatch.setattr(settings, "alexa_verify_signature", False)
    monkeypatch.setattr(settings, "alexa_skill_id", SKILL_ID)


def _request(
    kind: str,
    *,
    intent: str | None = None,
    slots: dict | None = None,
    token: str | None = None,
    skill_id: str = SKILL_ID,
    confirmation: str | None = None,
):
    request: dict = {
        "type": kind,
        "requestId": "amzn1.echo-api.request.1",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if intent:
        request["intent"] = {
            "name": intent,
            "slots": {
                name: {"name": name, "value": value} for name, value in (slots or {}).items()
            },
        }
        if confirmation:
            request["intent"]["confirmationStatus"] = confirmation
    return {
        "version": "1.0",
        "session": {
            "application": {"applicationId": skill_id},
            "user": {"userId": "amzn1.ask.account.1", **({"accessToken": token} if token else {})},
        },
        "context": {"System": {"application": {"applicationId": skill_id}}},
        "request": request,
    }


def _spoken(response: dict) -> str:
    return response["response"]["outputSpeech"]["text"]


# ── the three gates ────────────────────────────────────────────────────
async def test_a_request_for_another_developers_skill_is_refused(client):
    """However well signed, a request naming another skill id is not ours to answer."""
    response = await client.post(
        "/alexa", json=_request("LaunchRequest", skill_id="amzn1.ask.skill.someone-else")
    )
    assert response.status_code == 403


async def test_an_unlinked_speaker_gets_a_link_account_card_not_data(client, session, user):
    await GoalService(session).create_task(user.id, title="Secret project plan")
    await session.commit()

    response = await client.post("/alexa", json=_request("IntentRequest", intent="WhatIsDueIntent"))
    assert response.status_code == 200
    assert response.json()["response"]["card"] == {"type": "LinkAccount"}
    assert "Secret project plan" not in response.text


def _later_today(hours: float) -> datetime:
    """``now + hours``, clipped to the user's *local* day.

    "What is due today" is answered in Asia/Kolkata. A plain ``now + 3h`` crosses local
    midnight from 21:00 IST onward, and the skill would correctly answer "nothing today"
    — a time bomb that only goes off in the evening.
    """
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    local_now = datetime.now(ist)
    end_of_day = local_now.replace(hour=23, minute=59, second=0, microsecond=0)
    due = local_now + timedelta(hours=hours)
    return (due if due < end_of_day else end_of_day).astimezone(UTC)


async def test_a_linked_account_reaches_the_same_services_the_app_uses(client, session, user):
    access, _, _ = await IdentityService(session).issue_session(user)
    await GoalService(session).create_task(
        user.id, title="Submit the report", due_at=_later_today(3)
    )
    await session.commit()

    response = await client.post(
        "/alexa", json=_request("IntentRequest", intent="WhatIsDueIntent", token=access)
    )
    assert response.status_code == 200
    assert "Submit the report" in _spoken(response.json())


async def test_a_forged_access_token_does_not_link_an_account(client):
    response = await client.post(
        "/alexa",
        json=_request("IntentRequest", intent="WhatIsDueIntent", token="not.a.real.token"),
    )
    assert response.json()["response"]["card"] == {"type": "LinkAccount"}


# ── the seven intents ──────────────────────────────────────────────────
async def test_every_declared_intent_resolves_to_a_spoken_answer(session, user):
    """The simulator's check, run without the simulator: no intent falls through."""
    from jarvis.connectors.alexa.skill import INTENTS

    await GoalService(session).create_task(
        user.id, title="Read chapter four", due_at=_later_today(5)
    )
    await session.commit()

    for name in INTENTS:
        response = await handle_request(
            session,
            _request("IntentRequest", intent=name, slots={"taskTitle": "Read chapter four"}),
            user_id=user.id,
        )
        assert response["response"]["outputSpeech"]["text"], f"{name} said nothing"


async def test_adding_a_task_by_voice_stores_a_real_deadline(session, user):
    response = await handle_request(
        session,
        _request(
            "IntentRequest",
            intent="AddTaskIntent",
            slots={"taskTitle": "Buy the tickets", "dueDate": "2026-09-12", "dueTime": "17:30"},
        ),
        user_id=user.id,
    )
    await session.commit()

    tasks = await GoalService(session).session.scalars(_open_tasks(user.id))
    task = next(t for t in tasks if t.title == "Buy the tickets")
    assert task.due_at is not None
    assert "Buy the tickets" in _spoken(response)


def _open_tasks(user_id):
    from jarvis.db.models.domain import Task
    from sqlalchemy import select

    return select(Task).where(Task.user_id == user_id)


async def test_a_whole_day_voice_deadline_lands_at_the_end_of_the_day(session, user):
    """ "Due Friday" resolved to midnight would fire every reminder a day early."""
    await handle_request(
        session,
        _request(
            "IntentRequest",
            intent="AddTaskIntent",
            slots={"taskTitle": "Whole day thing", "dueDate": "2026-09-12"},
        ),
        user_id=user.id,
    )
    await session.commit()

    tasks = await session.scalars(_open_tasks(user.id))
    task = next(t for t in tasks if t.title == "Whole day thing")
    local = task.due_at.astimezone(__import__("zoneinfo").ZoneInfo(get_settings().timezone))
    assert (local.hour, local.minute) == (23, 59)


async def test_acknowledging_by_voice_cancels_the_later_alerts(session, user):
    goals = GoalService(session)
    task = await goals.create_task(
        user.id, title="Ack me", due_at=datetime.now(UTC) + timedelta(hours=26)
    )
    await session.commit()

    response = await handle_request(
        session,
        _request("IntentRequest", intent="AcknowledgeTaskIntent", slots={"taskTitle": "ack me"}),
        user_id=user.id,
    )
    await session.commit()
    assert "Acknowledged" in _spoken(response)

    # Acknowledging bumps the task version, which is what stands down every pending rung.
    from jarvis.db.models.ops import Schedule
    from sqlalchemy import select

    remaining = (
        await session.scalars(
            select(Schedule).where(Schedule.task_id == task.id, Schedule.status == "pending")
        )
    ).all()
    assert remaining == []


# ── approving out loud ─────────────────────────────────────────────────
async def _pending_approval(session, user, *, tool: str = "gmail.send"):
    proposal = await ToolGateway(session).propose(
        user.id,
        tool=tool,
        args={"to": "prof@example.edu", "subject": "Late", "body": "Running late."},
    )
    await session.commit()
    return proposal


async def test_approving_by_voice_needs_alexas_confirmation_turn_first(session, user):
    """A television can say "approve". The confirmation turn is not optional here."""
    proposal = await _pending_approval(session, user)

    response = await handle_request(
        session, _request("IntentRequest", intent="ApprovePendingIntent"), user_id=user.id
    )
    assert response["response"]["directives"][0]["type"] == "Dialog.ConfirmIntent"

    await session.refresh(proposal.approval)
    assert proposal.approval.decision is None, "nothing was decided by the un-confirmed turn"


async def test_a_confirmed_voice_approval_decides_the_same_row_the_app_would(session, user):
    proposal = await _pending_approval(session, user)

    response = await handle_request(
        session,
        _request("IntentRequest", intent="ApprovePendingIntent", confirmation="CONFIRMED"),
        user_id=user.id,
    )
    await session.commit()

    await session.refresh(proposal.approval)
    assert proposal.approval.decision == "approved"
    assert proposal.approval.decided_by == "alexa"
    assert "Approved" in _spoken(response)


async def test_an_action_needing_a_local_mac_confirmation_is_refused_by_voice(session, user):
    """R3 keeps its second factor. Voice cannot be the confirmation *and* the request."""
    await ToolGateway(session).propose(
        user.id, tool="mac.run_template", args={"template": "git.status", "params": {}}
    )
    await session.commit()

    response = await handle_request(
        session,
        _request("IntentRequest", intent="ApprovePendingIntent", confirmation="CONFIRMED"),
        user_id=user.id,
    )
    assert "Mac" in _spoken(response)


async def test_two_pending_approvals_send_the_user_to_the_app(session, user):
    await _pending_approval(session, user)
    await ToolGateway(session).propose(
        user.id, tool="slack.post_message", args={"channel": "#general", "text": "Running late."}
    )
    await session.commit()

    response = await handle_request(
        session,
        _request("IntentRequest", intent="ApprovePendingIntent", confirmation="CONFIRMED"),
        user_id=user.id,
    )
    assert "more than one" in _spoken(response)


# ── built-ins ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name", ["AMAZON.HelpIntent", "AMAZON.StopIntent", "AMAZON.FallbackIntent"]
)
async def test_the_required_built_in_intents_are_handled(session, user, name):
    response = await handle_request(
        session, _request("IntentRequest", intent=name), user_id=user.id
    )
    assert response["response"]["outputSpeech"]["text"]
