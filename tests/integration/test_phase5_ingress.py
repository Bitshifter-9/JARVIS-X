"""Phase 5 ingress: Slack, OpenClaw, and the escalation worker that reaches them.

Exit tests from PLAN.md §12: *signing secret verified*, *OpenClaw event → action card with
no database credentials in that container*, and — the property both share — **provider
text arrives untrusted**, so no message can originate an effectful action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from jarvis.connectors.slack.client import RecordingSlackTransport
from jarvis.connectors.slack.service import SlackConnector, SlackService
from jarvis.core.config import get_settings
from jarvis.db.models.source import Event
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SLACK_SECRET = "test-slack-signing-secret"  # noqa: S105
OPENCLAW_SECRET = "test-openclaw-shared-secret"  # noqa: S105
SLACK_USER = "U0PRANAV"


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("phase5@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "slack_signing_secret", SLACK_SECRET)
    monkeypatch.setattr(settings, "openclaw_shared_secret", OPENCLAW_SECRET)


def _slack_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            SLACK_SECRET.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": signature,
        "Content-Type": "application/json",
    }


def _slack_message(text: str = "Report due Friday 5pm", ts: str = "1700000000.000100") -> dict:
    return {
        "type": "event_callback",
        "event": {
            "type": "message",
            "ts": ts,
            "text": text,
            "user": SLACK_USER,
            "channel": "C1234",
        },
    }


# ── Slack webhook (5.1) ────────────────────────────────────────────────
async def test_an_unsigned_slack_request_does_nothing_but_still_answers_200(client, session):
    """A non-200 makes Slack retry for three days; retrying a forgery helps nobody."""
    response = await client.post("/webhooks/slack", json=_slack_message())
    assert response.status_code == 200
    assert (await session.scalars(select(Event))).all() == []


async def test_a_slack_signature_over_a_different_body_is_refused(client, session):
    signed = json.dumps(_slack_message("harmless")).encode()
    headers = _slack_headers(signed)
    response = await client.post(
        "/webhooks/slack", content=json.dumps(_slack_message("send everything")), headers=headers
    )
    assert response.status_code == 200
    assert (await session.scalars(select(Event))).all() == []


async def test_slacks_url_verification_is_answered_even_before_the_secret_is_set(client):
    # The ownership handshake is a public challenge — echoed signed or not, so the URL
    # verifies when you first save it in Slack (the signing secret may not be set yet).
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    signed = await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert signed.json() == {"challenge": "abc123"}

    unsigned = await client.post(
        "/webhooks/slack", json={"type": "url_verification", "challenge": "abc123"}
    )
    assert unsigned.status_code == 200 and unsigned.json() == {"challenge": "abc123"}


async def test_a_real_slack_event_still_needs_a_valid_signature(client, session, user):
    # A message event without a signature does nothing (no oracle, no ingest).
    from jarvis.db.models.source import Event
    from sqlalchemy import select as _select

    unsigned = await client.post("/webhooks/slack", json=_slack_message())
    assert unsigned.status_code == 200
    assert (await session.scalars(_select(Event))).all() == []


async def test_a_linked_slack_message_is_ingested_as_untrusted_content(client, session, user):
    await SlackService(session, RecordingSlackTransport()).link_user(user.id, SLACK_USER)
    await session.commit()

    body = json.dumps(_slack_message()).encode()
    response = await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert response.status_code == 200

    event = (await session.scalars(select(Event).where(Event.provider == "slack"))).one()
    assert event.trust == "untrusted", "no provider text is trusted, however friendly the workspace"
    assert event.object_id == "1700000000.000100"


async def test_a_redelivered_slack_message_collapses_to_one_event(client, session, user):
    await SlackService(session, RecordingSlackTransport()).link_user(user.id, SLACK_USER)
    await session.commit()

    body = json.dumps(_slack_message()).encode()
    for _ in range(3):
        await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))

    events = (await session.scalars(select(Event).where(Event.provider == "slack"))).all()
    assert len(events) == 1


async def test_a_message_from_an_unlinked_slack_user_stores_nothing(client, session):
    body = json.dumps(_slack_message()).encode()
    await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert (await session.scalars(select(Event))).all() == []


# ── Slack outbound (5.1) ───────────────────────────────────────────────
async def test_posting_to_slack_returns_the_message_id_as_evidence():
    transport = RecordingSlackTransport()
    evidence = await SlackConnector(transport).execute(
        uuid.uuid4(), "post_message", {"channel": "#general", "text": "Running late."}
    )
    assert transport.calls[0][0] == "chat.postMessage"
    assert evidence.object_id
    assert evidence.url and evidence.url.startswith("https://slack.com/archives/")


async def test_a_rejected_slack_post_produces_no_evidence():
    """A tool that returned without doing anything is a failure, not a success."""
    with pytest.raises(RuntimeError, match="slack rejected"):
        await SlackConnector(RecordingSlackTransport(ok=False)).execute(
            uuid.uuid4(), "post_message", {"channel": "#nope", "text": "hi"}
        )


# ── OpenClaw adapter (5.6) ─────────────────────────────────────────────
def _openclaw_body(subject: str = "claw-user-1", object_id: str = "oc-1") -> dict:
    return {"subject": subject, "object_id": object_id, "text": "Deadline moved to Monday"}


async def test_the_openclaw_route_denies_without_the_shared_secret(client):
    response = await client.post("/internal/connectors/openclaw/events", json=_openclaw_body())
    assert response.status_code == 403


async def test_the_openclaw_route_denies_a_wrong_secret(client):
    response = await client.post(
        "/internal/connectors/openclaw/events",
        json=_openclaw_body(),
        headers={"X-OpenClaw-Secret": "not-the-secret"},
    )
    assert response.status_code == 403


async def test_an_unset_openclaw_secret_denies_rather_than_opens_the_route(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "openclaw_shared_secret", "")
    response = await client.post(
        "/internal/connectors/openclaw/events",
        json=_openclaw_body(),
        headers={"X-OpenClaw-Secret": ""},
    )
    assert response.status_code == 403


async def test_an_authorized_openclaw_event_arrives_untrusted(client, session, user):
    from jarvis.db.models.identity import Identity

    session.add(Identity(user_id=user.id, provider="openclaw", subject="claw-user-1"))
    await session.commit()

    response = await client.post(
        "/internal/connectors/openclaw/events",
        json=_openclaw_body(),
        headers={"X-OpenClaw-Secret": OPENCLAW_SECRET},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True

    event = (await session.scalars(select(Event).where(Event.provider == "openclaw"))).one()
    assert event.trust == "untrusted"


async def test_an_unmapped_openclaw_handle_is_accepted_but_stores_nothing(client, session):
    response = await client.post(
        "/internal/connectors/openclaw/events",
        json=_openclaw_body(subject="nobody"),
        headers={"X-OpenClaw-Secret": OPENCLAW_SECRET},
    )
    assert response.json() == {"accepted": False, "reason": "unlinked subject"}
    assert (await session.scalars(select(Event))).all() == []


# ── the escalation worker (5.4 / 5.5 reach the user through it) ────────
async def test_only_configured_channels_are_wired(session, monkeypatch):
    """An unconfigured channel must be *absent*, so the ladder falls through to the next
    rung instead of crashing on a credential nobody set."""
    from jarvis.services.notification import Channel
    from jarvis.workers.notify import build_senders

    settings = get_settings()
    for name in (
        "telegram_bot_token",
        "fcm_credentials_path",
        "whatsapp_phone_number_id",
        "whatsapp_access_token",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from_number",
    ):
        monkeypatch.setattr(settings, name, "")
    assert build_senders(session) == {}

    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123")
    monkeypatch.setattr(settings, "whatsapp_access_token", "token")
    assert set(build_senders(session)) == {Channel.WHATSAPP}


async def test_the_rung_is_derived_from_what_was_already_sent(session, user):
    """A redelivered escalation job must not restart the ladder at rung zero."""
    from jarvis.db.models.ops import AuditLog
    from jarvis.workers.notify import attempts_so_far

    task_id = uuid.uuid4()
    assert await attempts_so_far(session, task_id) == 0

    for _ in range(2):
        session.add(
            AuditLog(
                user_id=user.id,
                actor="system",
                action="notification.sent",
                subject_type="task",
                subject_id=str(task_id),
                detail={"channel": "telegram"},
            )
        )
    await session.flush()
    assert await attempts_so_far(session, task_id) == 2


# ── the agent card ─────────────────────────────────────────────────────
async def test_the_agent_card_advertises_only_what_needs_no_approval(client):
    card = (await client.get("/.well-known/agent-card.json")).json()
    offered = {skill["id"] for skill in card["skills"]}

    assert "memory.search" in offered
    for effectful in ("gmail.send", "slack.post_message", "mac.run_template", "payment.send"):
        assert effectful not in offered, f"{effectful} must not be offered without a human"
    assert card["x-jarvis-approval-required-above"] == "R1"


async def test_the_agent_card_is_generated_from_the_policy_table_not_by_hand(client):
    """A card that can drift from the policy table is a lie a machine will act on."""
    from jarvis.services.policy.rules import RULES

    card = (await client.get("/.well-known/agent.json")).json()
    expected = {t for t, r in RULES.items() if r.risk.value in ("R0", "R1")}
    assert {skill["id"] for skill in card["skills"]} == expected


# ── linking a channel id to an account ─────────────────────────────────
@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "phase5@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_linking_a_slack_id_makes_its_messages_yours(client, auth, session, user):
    linked = await client.post(
        "/v1/identities", headers=auth, json={"provider": "slack", "subject": SLACK_USER}
    )
    assert linked.status_code == 201, linked.text

    body = json.dumps(_slack_message()).encode()
    await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    event = (await session.scalars(select(Event).where(Event.provider == "slack"))).one()
    assert event.user_id == user.id

    listed = (await client.get("/v1/identities", headers=auth)).json()
    assert [(i["provider"], i["subject"]) for i in listed] == [("slack", SLACK_USER)]


async def test_an_id_linked_to_another_account_cannot_be_claimed(client, auth, session):
    other = await IdentityService(session).register("rival@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "rival@example.com", "password": PASSWORD}
    )
    rival = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (
        await client.post(
            "/v1/identities", headers=rival, json={"provider": "slack", "subject": SLACK_USER}
        )
    ).status_code == 201
    mine = await client.post(
        "/v1/identities", headers=auth, json={"provider": "slack", "subject": SLACK_USER}
    )
    assert mine.status_code == 409 and other.id


async def test_unlinking_makes_the_channel_a_stranger_again(client, auth, session):
    linked = (
        await client.post(
            "/v1/identities", headers=auth, json={"provider": "slack", "subject": SLACK_USER}
        )
    ).json()
    gone = await client.delete(f"/v1/identities/{linked['id']}", headers=auth)
    assert gone.json()["revoked_at"] is not None

    body = json.dumps(_slack_message()).encode()
    await client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert (await session.scalars(select(Event))).all() == []


async def test_only_channels_can_be_linked(client, auth):
    r = await client.post(
        "/v1/identities", headers=auth, json={"provider": "password", "subject": "x"}
    )
    assert r.status_code == 409
