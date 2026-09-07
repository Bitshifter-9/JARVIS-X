"""Deadlines you can see, briefings that read well, keys that stop being retried."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core.ids import new_correlation_id
from jarvis.db.queue import JobQueue
from jarvis.llm.health import ProviderHealthStore as Health
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services.briefing import compose, fallback, gather
from jarvis.services.event import EventService
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from jarvis.services.routines import RoutineService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select, text

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "Running late on the submission."}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("brief@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "brief@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mail_task(session, user):
    events = EventService(session)
    source = await events.upsert_source_object(
        user_id=user.id, provider="gmail", object_id="m-exam", kind="email",
        title="Meeting", excerpt="You have exam deadline on 9 September 2026 at 9 am",
        author="Saritha Bandaram <s@gmail.com>", occurred_at=datetime.now(UTC),
        url="https://mail.google.com/mail/u/0/#inbox/m-exam",
    )
    task = await GoalService(session).create_task(
        user.id, title="Exam", due_at=datetime.now(UTC) + timedelta(days=2),
        timezone="Asia/Kolkata", source_id=source.id, confidence=1.0,
        evidence_span="exam deadline on 9 September 2026 at 9 am",
    )
    await session.commit()
    return task


# ── tasks you can see ─────────────────────────────────────────────────
async def test_tasks_list_shows_where_each_deadline_came_from(client, auth, session, user):
    await _mail_task(session, user)
    await GoalService(session).create_task(user.id, title="No date", due_at=None)
    await session.commit()

    r = await client.get("/v1/tasks", headers=auth)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [t["title"] for t in rows] == ["Exam", "No date"]  # dated first
    exam = rows[0]
    assert exam["source_provider"] == "gmail"
    assert exam["source_author"].startswith("Saritha")
    assert exam["source_title"] == "Meeting"
    assert exam["evidence_span"].startswith("exam deadline")

    done = await client.patch(f"/v1/tasks/{exam['id']}", json={"status": "done"}, headers=auth)
    assert done.status_code == 200
    assert [t["title"] for t in (await client.get("/v1/tasks", headers=auth)).json()] == ["No date"]
    assert len((await client.get("/v1/tasks?status=all", headers=auth)).json()) == 2


async def test_expired_approvals_are_not_listed_as_pending(client, auth, session, user):
    proposal = await ToolGateway(session).propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    assert [a["id"] for a in (await client.get("/v1/approvals", headers=auth)).json()] == [
        str(proposal.approval.id)
    ]
    await session.execute(
        text("UPDATE approvals SET expires_at = now() - interval '1 minute' WHERE id = :id"),
        {"id": proposal.approval.id},
    )
    await session.commit()
    assert (await client.get("/v1/approvals", headers=auth)).json() == []


# ── briefings ─────────────────────────────────────────────────────────
class Briefer:
    name = "briefer"
    is_paid = False
    model = "briefer"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.prompts.append(request.messages[-1].content)
        return LLMResponse(text=self.reply, provider=self.name, model=self.model)


def _router(session, model):
    return LLMRouter(
        session, providers={model.name: model}, cascade={c: (model.name,) for c in CallClass}
    )


async def test_a_briefing_is_prose_over_gathered_context(session, user):
    await _mail_task(session, user)
    context = await gather(session, user.id)
    assert context["due_within_7_days"][0]["title"] == "Exam"
    assert context["due_within_7_days"][0]["from"].startswith("gmail · Saritha")
    assert context["recent_mail"][0]["subject"] == "Meeting"

    model = Briefer("Good morning. Your exam is on Thursday at nine; nothing else is due.")
    text_out = await compose(session, user.id, "Brief me", router=_router(session, model))
    assert text_out.startswith("Good morning.")
    assert '"title": "Exam"' in model.prompts[0]  # the model saw the real deadline


async def test_a_garbled_or_missing_answer_falls_back_to_facts(session, user):
    await _mail_task(session, user)
    garbled = Briefer("2-3 natural spoken sentences without markdown/lists):**")
    text_out = await compose(session, user.id, "Brief me", router=_router(session, garbled))
    assert "1 thing(s) due this week; first is Exam" in text_out
    assert fallback({"due_within_7_days": []}).startswith("Nothing is due")


async def test_builtin_routines_fire_in_brief_mode_and_custom_ones_can_too(session, user):
    service = RoutineService(session)
    rows = await service.list(user.id)  # seeds the built-ins
    morning = next(r for r in rows if r.kind == "morning_briefing")
    await service.fire(morning)
    custom = await service.create(user.id, name="Do", prompt="open chrome", cron="0 9 * * *")
    await service.fire(custom)
    told = await service.create(
        user.id, name="Tell", prompt="what is due", cron="0 9 * * *", kind="brief"
    )
    await service.fire(told)
    await session.commit()
    from jarvis.db.models.job import Job

    jobs = (await session.scalars(select(Job))).all()
    modes = {j.payload["reply_to"]["title"]: j.payload["mode"] for j in jobs}
    assert modes == {"Morning briefing": "brief", "Do": "agent", "Tell": "brief"}


# ── the breaker and the lease ─────────────────────────────────────────
async def test_a_rejected_key_opens_the_breaker_for_an_hour(session):
    from jarvis.llm.providers import ProviderPermanentError

    class Rejecting:
        name = "groq"
        is_paid = False
        model = "groq"

        def is_configured(self) -> bool:
            return True

        def supports(self, call_class: CallClass) -> bool:
            return True

        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise ProviderPermanentError("groq rejected the request: Invalid API Key")

    ok = Briefer("fine")
    router = LLMRouter(
        session, providers={"groq": Rejecting(), ok.name: ok},
        cascade={c: ("groq", ok.name) for c in CallClass},
    )
    from jarvis.llm.types import Message

    response = await router.chat([Message("user", "hi")])
    assert response.provider == "briefer"
    cooling = await Health(session).cooling_down()
    assert "groq" in cooling
    assert cooling["groq"] > datetime.now(UTC) + timedelta(minutes=50)


async def test_the_agent_worker_holds_a_longer_lease(session, user):
    queue = JobQueue(session)
    await queue.enqueue("agent.run", {"user_id": str(user.id), "text": "x"}, user_id=user.id)
    await session.commit()
    (job,) = await queue.claim("w", limit=1, kinds=["agent.run"], visibility_seconds=900)
    assert job.visible_at > datetime.now(UTC) + timedelta(minutes=10)


async def test_clipboard_verbs_are_r1_on_the_paired_phone(session, user):
    from jarvis.services.device import DeviceService, generate_keypair, sign

    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id, name="Pixel", platform="android", public_key_pem=public_pem
    )
    await session.commit()
    phone = await devices.complete_pairing(
        user.id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    p = await ToolGateway(session).propose(
        user.id, tool="phone.clipboard_write", args={"text": "hello"}, device_id=phone.id
    )
    assert p.policy.risk.value == "R1" and not p.needs_approval
    _ = new_correlation_id()
