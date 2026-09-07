"""Activity, the focus guard and the learning loop (PLAN.md 10.6.3–10.6.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.core.config import get_settings
from jarvis.db.models.agent import Action
from jarvis.db.models.domain import WorkSession
from jarvis.db.models.ops import ActivitySample, AuditLog
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import CallClass, LLMRequest, LLMResponse
from jarvis.services import activity
from jarvis.services.device import DeviceService, generate_keypair, sign
from jarvis.services.identity import IdentityService
from jarvis.services.profile import get_profile
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("focus@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "focus@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def mac(session, user):
    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(user.id, name="MacBook", public_key_pem=public_pem)
    await session.commit()
    device = await devices.complete_pairing(
        user.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


@pytest.fixture
def guard_on():
    s = get_settings()
    before = (s.focus_guard_enabled, s.focus_guard_minutes)
    s.focus_guard_enabled, s.focus_guard_minutes = True, 10
    yield s
    s.focus_guard_enabled, s.focus_guard_minutes = before


def _block(user_id, now):
    return WorkSession(user_id=user_id, started_at=now - timedelta(minutes=20), source="focus")


def _drift(user_id, device_id, now, minutes=10, app="Google Chrome", title="YouTube - cat videos"):
    return [
        ActivitySample(
            user_id=user_id, device_id=device_id, platform="macos", app=app, title=title,
            at=now - timedelta(seconds=30 * i),
        )
        for i in range(minutes * 2)
    ]


# ── samples and the query ─────────────────────────────────────────────
async def test_a_device_reports_activity_and_the_query_groups_it(client, auth, session, user, mac):
    now = datetime.now(UTC)
    body = [
        {"app": "Xcode", "title": "JARVIS.swift", "at": (now - timedelta(minutes=i)).isoformat()}
        for i in range(6)
    ] + [
        {"app": "Slack", "title": "#general", "at": now.isoformat()},
        {"app": "Slack", "title": "#general", "at": (now - timedelta(seconds=30)).isoformat()},
    ]
    r = await client.post(f"/v1/devices/{mac.id}/activity", json=body, headers=auth)
    assert r.status_code == 202 and r.json() == {"stored": 8}

    grouped = await activity.summary(
        session, user.id, now - timedelta(hours=1), now + timedelta(minutes=1)
    )
    assert grouped[0]["app"] == "Xcode" and grouped[0]["minutes"] == 3
    assert grouped[0]["titles"] == ["JARVIS.swift"]
    assert grouped[1]["app"] == "Slack"


async def test_activity_is_owner_scoped_and_pruned(client, auth, session, user, mac):
    other = await IdentityService(session).register("other@example.com", PASSWORD)
    await session.commit()
    private_pem, public_pem = generate_keypair()
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(other.id, name="Theirs", public_key_pem=public_pem)
    await session.commit()
    theirs = await devices.complete_pairing(
        other.id, challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    r = await client.post(f"/v1/devices/{theirs.id}/activity", json=[{"app": "x"}], headers=auth)
    assert r.status_code == 403

    old = ActivitySample(
        user_id=user.id, platform="macos", app="Old", at=datetime.now(UTC) - timedelta(days=40)
    )
    session.add(old)
    await session.commit()
    assert await activity.prune(session) == 1


# ── the focus guard ───────────────────────────────────────────────────
async def test_the_guard_never_fires_outside_a_focus_block(session, user, mac, guard_on):
    now = datetime.now(UTC)
    session.add_all(_drift(user.id, mac.id, now))
    await session.commit()
    assert (await activity.focus_guard(session, user.id, now=now))["skipped"] == "no focus block"
    assert await session.scalar(select(Action)) is None


async def test_the_guard_climbs_notify_speak_lock_inside_a_block(session, user, mac, guard_on):
    now = datetime.now(UTC)
    session.add(_block(user.id, now))
    session.add_all(_drift(user.id, mac.id, now))
    await session.commit()

    tiers = []
    for _ in range(3):
        outcome = await activity.focus_guard(session, user.id, now=now)
        await session.commit()
        tiers.append((outcome["tier"], outcome["tool"]))
    assert tiers == [(1, "mac.notify"), (2, "mac.say"), (3, "mac.lock_screen")]

    actions = (await session.scalars(select(Action))).all()
    assert {a.tool for a in actions} == {"mac.notify", "mac.say", "mac.lock_screen"}
    assert len(actions) == 3
    assert all(a.device_id == mac.id for a in actions)  # addressed to the Mac that drifted
    notify = next(a for a in actions if a.tool == "mac.notify")
    assert "YouTube" not in notify.args["body"] and "Google Chrome" in notify.args["body"]
    nudged = select(AuditLog).where(AuditLog.action == "focus.nudged")
    nudges = (await session.scalars(nudged)).all()
    assert sorted(n.detail["tier"] for n in nudges) == [1, 2, 3]


async def test_the_guard_stays_quiet_when_on_task_or_switched_off(session, user, mac, guard_on):
    now = datetime.now(UTC)
    session.add(_block(user.id, now))
    session.add_all(_drift(user.id, mac.id, now, app="Xcode", title="main.swift"))
    await session.commit()
    assert (await activity.focus_guard(session, user.id, now=now))["skipped"] == "on task"

    guard_on.focus_guard_enabled = False
    session.add_all(_drift(user.id, mac.id, now))
    await session.commit()
    assert (await activity.focus_guard(session, user.id, now=now))["skipped"] == "off"
    assert await session.scalar(select(Action)) is None


# ── the learning loop ─────────────────────────────────────────────────
class Suggesting:
    name = "suggesting"
    is_paid = False
    model = "suggesting"

    def is_configured(self) -> bool:
        return True

    def supports(self, call_class: CallClass) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        text = json.dumps(
            {
                "suggestions": [
                    {
                        "section": "priorities",
                        "text": "You spend your evenings in Xcode on JARVIS X.",
                        "because": "3 hours in Xcode",
                    },
                    {
                        "section": "decisions",
                        "text": "You approve Slack posts quickly.",
                        "because": "2/2",
                    },
                ]
            }
        )
        return LLMResponse(text=text, provider=self.name, model=self.model)


def _router(session):
    m = Suggesting()
    return LLMRouter(session, providers={m.name: m}, cascade={c: (m.name,) for c in CallClass})


async def test_the_nightly_loop_suggests_once_and_the_owner_decides(
    client, auth, session, user, mac
):
    tonight = datetime.now(UTC).astimezone().replace(hour=22, minute=30).astimezone(UTC)
    # Evidence: an evening in Xcode.
    session.add_all(_drift(user.id, mac.id, tonight, minutes=30, app="Xcode", title="main.swift"))
    await session.commit()

    morning = tonight.astimezone().replace(hour=9).astimezone(UTC)
    early = await activity.learn(session, user.id, router=_router(session), now=morning)
    assert early == {"skipped": "too early"}

    outcome = await activity.learn(session, user.id, router=_router(session), now=tonight)
    await session.commit()
    assert outcome["added"] == 2
    again = await activity.learn(session, user.id, router=_router(session), now=tonight)
    assert again == {"skipped": "done today"}

    pending = (await client.get("/v1/profile/suggestions", headers=auth)).json()
    assert [p["section"] for p in pending] == ["priorities", "decisions"]

    accepted = await client.post(
        f"/v1/profile/suggestions/{pending[0]['id']}/accept", headers=auth
    )
    assert accepted.status_code == 200
    assert "evenings in Xcode" in accepted.json()["priorities"]
    dismissed = await client.post(
        f"/v1/profile/suggestions/{pending[1]['id']}/dismiss", headers=auth
    )
    assert dismissed.status_code == 200 and "Slack posts" not in dismissed.json()["decisions"]
    assert (await client.get("/v1/profile/suggestions", headers=auth)).json() == []

    profile = await get_profile(session, user.id)
    await session.refresh(profile)
    dismissed_texts = [d["text"] for d in profile.suggestions["dismissed"]]
    assert dismissed_texts == ["You approve Slack posts quickly."]
    missing = await client.post("/v1/profile/suggestions/nope/accept", headers=auth)
    assert missing.status_code == 404
