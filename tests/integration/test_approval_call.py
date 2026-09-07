"""Approval by phone call and the wake-up call (PLAN.md 10.2).

An unanswered approval pushes at once and rings later; "yes" on the call decides it
through the same gate as the app; a forged or reused callback changes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.connectors.twilio.client import TwilioCaller, twilio_signature
from jarvis.connectors.twilio.voice import approval_call_url, interpret, routine_call_url
from jarvis.core.config import get_settings
from jarvis.db.models.agent import Approval
from jarvis.db.models.job import Job
from jarvis.db.models.ops import AuditLog, NotificationEndpoint
from jarvis.services.identity import IdentityService
from jarvis.services.notification import Channel
from jarvis.services.routines import RoutineService
from jarvis.services.tool_gateway import ToolGateway
from jarvis.workers.notify import handle_approval
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}
AUTH_TOKEN = "twilio-test-auth-token"  # noqa: S105


class RecordingCaller(TwilioCaller):
    def __init__(self) -> None:
        super().__init__("AC1", AUTH_TOKEN, "+10000000000", transport=self._record)
        self.calls: list[dict[str, str]] = []

    async def _record(self, data: dict[str, str]) -> dict:
        self.calls.append(data)
        return {"sid": f"CA{len(self.calls)}", "status": "queued"}


class RecordingPush:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, address, *, title, body, task_id=None):  # noqa: ANN001
        self.sent.append((address, title, body))


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("call@example.com", PASSWORD)
    session.add(NotificationEndpoint(user_id=u.id, channel="push", address="fcm-token"))
    session.add(NotificationEndpoint(user_id=u.id, channel="call", address="+919999999999"))
    await session.commit()
    return u


@pytest.fixture
def twilio_settings():
    s = get_settings()
    before = (s.twilio_auth_token, s.twilio_call_for_approval_after_minutes, s.quiet_hours)
    s.twilio_auth_token = AUTH_TOKEN
    s.twilio_call_for_approval_after_minutes = 5
    s.quiet_hours = None  # the test must not depend on the wall clock
    yield s
    s.twilio_auth_token, s.twilio_call_for_approval_after_minutes, s.quiet_hours = before


@pytest.fixture
async def pending(session, user):
    proposal = await ToolGateway(session).propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    assert proposal.needs_approval
    return proposal.approval


async def _jobs(session, stage: str) -> list[Job]:
    rows = (await session.scalars(select(Job).where(Job.kind == "approval.escalate"))).all()
    return [j for j in rows if (j.payload or {}).get("stage") == stage]


def _signed(url: str, form: dict[str, str]) -> dict[str, str]:
    return {"X-Twilio-Signature": twilio_signature(AUTH_TOKEN, url, form)}


# ── the escalation ────────────────────────────────────────────────────
async def test_an_approval_pushes_now_and_schedules_the_call(
    session, user, pending, twilio_settings
):
    notify_jobs = await _jobs(session, "notify")
    assert len(notify_jobs) == 1

    push, caller = RecordingPush(), RecordingCaller()
    senders = {Channel.PUSH: push, Channel.CALL: caller}
    outcome = await handle_approval(session, notify_jobs[0], senders=senders)
    await session.commit()
    assert outcome["pushed"] is True
    assert push.sent[0][1] == "Approval needed" and "message.send" in push.sent[0][2]
    assert caller.calls == []  # not yet

    call_jobs = await _jobs(session, "call")
    assert len(call_jobs) == 1
    assert call_jobs[0].visible_at >= datetime.now(UTC) + timedelta(minutes=4)


async def test_the_call_stage_rings_once_and_not_after_a_decision(
    session, user, pending, twilio_settings
):
    notify_jobs = await _jobs(session, "notify")
    caller = RecordingCaller()
    await handle_approval(session, notify_jobs[0], senders={Channel.CALL: caller})
    await session.commit()
    call_job = (await _jobs(session, "call"))[0]

    outcome = await handle_approval(session, call_job, senders={Channel.CALL: caller})
    await session.commit()
    assert outcome == {"stage": "call", "called": True}
    assert len(caller.calls) == 1
    assert caller.calls[0]["To"] == "+919999999999"
    assert caller.calls[0]["Url"] == approval_call_url(pending.id)
    assert "Twiml" not in caller.calls[0]
    sent = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "notification.sent", AuditLog.subject_id == str(pending.id)
        )
    )
    assert sent.detail["channel"] == "call"

    # Decided in the app meanwhile: a redelivered call job does nothing.
    await ToolGateway(session).decide(user.id, pending.id, approved=True, decided_by="app")
    await session.commit()
    outcome = await handle_approval(session, call_job, senders={Channel.CALL: caller})
    assert outcome == {"skipped": "decided or gone"}
    assert len(caller.calls) == 1


async def test_the_daily_call_cap_holds_for_approvals(session, user, pending, twilio_settings):
    for _ in range(twilio_settings.max_calls_per_day):
        session.add(
            AuditLog(
                user_id=user.id,
                actor="system",
                action="notification.sent",
                detail={"channel": "call", "attempt": 0},
            )
        )
    await session.commit()
    caller = RecordingCaller()
    job = Job(
        kind="approval.escalate",
        payload={"approval_id": str(pending.id), "stage": "call"},
        user_id=user.id,
    )
    outcome = await handle_approval(session, job, senders={Channel.CALL: caller})
    assert outcome["skipped"] == "daily call cap"
    assert caller.calls == []


# ── the script and the answer ─────────────────────────────────────────
async def test_the_script_reads_the_action_and_gathers(client, session, pending, twilio_settings):
    url = approval_call_url(pending.id)
    path = url.removeprefix(twilio_settings.base_url.rstrip("/"))
    form = {"CallSid": "CA1", "From": "+919999999999"}
    r = await client.post(path, data=form, headers=_signed(url, form))
    assert r.status_code == 200, r.text
    assert "<Gather" in r.text and "message.send" in r.text
    assert f'action="{url}/decide"' in r.text

    # Wrong token: no script, no summary.
    bad = path.rsplit("/", 1)[0] + "/" + "0" * 32
    r = await client.post(bad, data=form, headers=_signed(url, form))
    assert r.status_code == 403 and "message.send" not in r.text


async def test_yes_on_the_call_approves_through_the_gate(
    client, session, user, pending, twilio_settings
):
    url = approval_call_url(pending.id) + "/decide"
    path = url.removeprefix(twilio_settings.base_url.rstrip("/"))
    form = {"CallSid": "CA1", "Digits": "1"}
    r = await client.post(path, data=form, headers=_signed(url, form))
    assert r.status_code == 200 and "Approved" in r.text

    approval = await session.get(Approval, pending.id)
    await session.refresh(approval)
    assert approval.decision == "approved" and approval.decided_by == "phone"
    resume = await session.scalar(select(Job).where(Job.kind == "run.resume"))
    assert resume is not None and resume.payload["approved"] is True

    # Reusing the same callback changes nothing: the gate refuses a second decision.
    again = {**form, "Digits": "2"}
    r = await client.post(path, data=again, headers=_signed(url, again))
    assert r.status_code == 200 and "already" in r.text
    await session.refresh(approval)
    assert approval.decision == "approved"


async def test_a_forged_callback_decides_nothing(client, session, pending, twilio_settings):
    url = approval_call_url(pending.id) + "/decide"
    path = url.removeprefix(twilio_settings.base_url.rstrip("/"))
    form = {"CallSid": "CA1", "Digits": "1"}
    r = await client.post(path, data=form, headers={"X-Twilio-Signature": "bogus"})
    assert r.status_code == 403
    r = await client.post(path, data=form)  # no signature at all
    assert r.status_code == 403
    approval = await session.get(Approval, pending.id)
    await session.refresh(approval)
    assert approval.decision is None


async def test_no_on_the_call_rejects_and_a_mumble_asks_again(
    client, session, pending, twilio_settings
):
    url = approval_call_url(pending.id) + "/decide"
    path = url.removeprefix(twilio_settings.base_url.rstrip("/"))
    mumble = {"CallSid": "CA1", "SpeechResult": "hmm what"}
    r = await client.post(path, data=mumble, headers=_signed(url, mumble))
    assert r.status_code == 200 and "<Gather" in r.text
    approval = await session.get(Approval, pending.id)
    await session.refresh(approval)
    assert approval.decision is None

    no = {"CallSid": "CA1", "SpeechResult": "No, don't approve that"}
    r = await client.post(path, data=no, headers=_signed(url, no))
    assert "Rejected" in r.text
    await session.refresh(approval)
    assert approval.decision == "rejected" and approval.decided_by == "phone"


def test_interpretation_prefers_no_over_yes_in_one_breath():
    assert interpret("1", None) is True
    assert interpret("2", None) is False
    assert interpret("9", None) is None
    assert interpret(None, "yes go ahead") is True
    assert interpret(None, "No, don't approve") is False
    assert interpret(None, "") is None


# ── the wake-up call ──────────────────────────────────────────────────
async def test_the_wake_up_call_speaks_the_briefing_and_snoozes(
    client, session, user, twilio_settings
):
    routine = await RoutineService(session).create(
        user.id, name="Morning briefing", prompt="brief me", cron="0 7 * * *", channel="call"
    )
    routine.last_result = "Two things due today and nothing at risk."
    await session.commit()

    url = routine_call_url(routine.id)
    path = url.removeprefix(twilio_settings.base_url.rstrip("/"))
    form = {"CallSid": "CA2"}
    r = await client.post(path, data=form, headers=_signed(url, form))
    assert r.status_code == 200
    assert "Two things due today" in r.text and "<Gather" in r.text

    snooze = {"CallSid": "CA2", "Digits": "1"}
    r = await client.post(path + "/snooze", data=snooze, headers=_signed(url + "/snooze", snooze))
    assert "Snoozed" in r.text
    job = await session.scalar(select(Job).where(Job.kind == "agent.run"))
    assert job is not None
    assert job.visible_at >= datetime.now(UTC) + timedelta(minutes=9)
    assert job.payload["reply_to"]["channel"] == "call"
