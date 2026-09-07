"""Slack history scan (FEATURES: scan Slack like Gmail, every 4h and on demand)."""

from __future__ import annotations

import pytest
from jarvis.connectors.slack.client import RecordingSlackTransport
from jarvis.connectors.slack.service import SlackService, scan_slack
from jarvis.db.models.source import Event
from jarvis.services.identity import IdentityService
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SLACK_USER = "U0FRIEND"


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("scan@example.com", PASSWORD)
    await session.commit()
    return u


def _msg(ts: str, text: str, user: str = SLACK_USER) -> dict:
    return {"type": "message", "ts": ts, "text": text, "user": user}


async def test_scan_ingests_deadlines_from_a_group_dm_for_linked_users(session, user):
    await SlackService(session, RecordingSlackTransport()).link_user(user.id, SLACK_USER)
    await session.commit()

    transport = RecordingSlackTransport(
        channels=[
            {"id": "G_GROUP", "is_member": True},  # a group DM
            {"id": "C_OTHER", "is_member": False},  # not a member → skipped
        ],
        history={
            "G_GROUP": [
                _msg("1700000001.1", "the report is due Friday 5pm"),
                _msg("1700000002.2", "lol ok", user="U0STRANGER"),  # unlinked → skipped
            ]
        },
    )
    result = await scan_slack(session, transport)
    await session.commit()
    assert result == {"channels": 1, "new": 1}

    events = (await session.scalars(select(Event).where(Event.provider == "slack"))).all()
    assert len(events) == 1 and events[0].user_id == user.id
    assert "report is due Friday" in events[0].payload["text"]

    # A second scan of the same window ingests nothing (idempotent on ts).
    again = await scan_slack(session, transport)
    assert again["new"] == 0


async def test_scan_does_nothing_without_a_linked_user(session, user):
    transport = RecordingSlackTransport(
        channels=[{"id": "C1", "is_member": True}],
        history={"C1": [_msg("1700000003.3", "due tomorrow")]},
    )
    assert await scan_slack(session, transport) == {"channels": 0, "new": 0}
    # conversations.list is not even called when nobody is linked.
    assert transport.calls == []


async def test_the_four_hour_default(session):
    from jarvis.core.config import get_settings

    assert get_settings().gmail_poll_seconds == 14400  # 4 hours
