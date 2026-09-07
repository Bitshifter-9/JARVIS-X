"""Phase 5 connectors: the parts that must be right without a network.

Every one of these is a boundary. A signature check that accepts a forged request, a due
date parsed a day early, or a base URL that resolves inside the host network are all
failures nobody notices until they matter, so they are tested here rather than against a
live provider that cannot be replayed.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from jarvis.connectors.canvas.client import _checked_base_url, normalize_assignment
from jarvis.connectors.google.classroom import coursework_due_at, normalize_coursework
from jarvis.connectors.slack.client import verify_slack_signature
from jarvis.connectors.slack.service import normalize_message
from jarvis.connectors.twilio.client import twiml_for
from jarvis.connectors.whatsapp.client import template_payload

SECRET = "slack-signing-secret"  # noqa: S105
NOW = 1_700_000_000.0


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    return (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )


# ── Slack request signing (5.1) ────────────────────────────────────────
def test_a_correctly_signed_slack_request_is_accepted():
    body, ts = b'{"type":"event_callback"}', "1700000000"
    assert verify_slack_signature(
        SECRET, timestamp=ts, body=body, signature=_sign(body, ts), now=NOW
    )


def test_a_tampered_body_fails_even_with_a_real_signature():
    ts = "1700000000"
    signature = _sign(b'{"text":"hello"}', ts)
    assert not verify_slack_signature(
        SECRET,
        timestamp=ts,
        body=b'{"text":"transfer everything"}',
        signature=signature,
        now=NOW,
    )


def test_a_replayed_request_expires_even_though_its_signature_never_does():
    body, ts = b"{}", "1700000000"
    signature = _sign(body, ts)
    # Six minutes later: the signature still verifies, the timestamp does not.
    assert not verify_slack_signature(
        SECRET, timestamp=ts, body=body, signature=signature, now=NOW + 360
    )


@pytest.mark.parametrize(
    ("secret", "signature"),
    [("", "v0=whatever"), (SECRET, None), (SECRET, "v0=deadbeef"), (SECRET, "not-a-signature")],
)
def test_an_unconfigured_or_missing_signature_denies_rather_than_allows(secret, signature):
    assert not verify_slack_signature(
        secret, timestamp="1700000000", body=b"{}", signature=signature, now=NOW
    )


# ── Slack normalization (5.1) ──────────────────────────────────────────
def test_a_slack_message_becomes_one_sync_item():
    item = normalize_message(
        {
            "type": "message",
            "ts": "1700000000.000100",
            "text": "Report due Friday 5pm",
            "user": "U123",
            "channel": "C999",
        }
    )
    assert item is not None
    assert item.object_id == "1700000000.000100"
    assert item.body == "Report due Friday 5pm"
    assert item.occurred_at == datetime.fromtimestamp(1700000000.0001, tz=UTC)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "message", "ts": "1.0", "text": "hi", "bot_id": "B1"},
        {"type": "message", "ts": "1.0", "text": "edited", "subtype": "message_changed"},
        {"type": "reaction_added", "ts": "1.0"},
        {"type": "message", "ts": "1.0"},
    ],
)
def test_slack_echoes_and_non_messages_are_not_ingested(event):
    """Ingesting our own post would let the assistant answer itself."""
    assert normalize_message(event) is None


# ── Classroom (5.2) ────────────────────────────────────────────────────
def test_a_whole_day_classroom_deadline_is_the_end_of_that_day():
    """Midnight would move every whole-day deadline a full day earlier."""
    due = coursework_due_at({"dueDate": {"year": 2026, "month": 9, "day": 12}})
    assert due == datetime(2026, 9, 12, 23, 59, tzinfo=UTC)


def test_a_classroom_deadline_with_a_time_keeps_that_time():
    due = coursework_due_at(
        {"dueDate": {"year": 2026, "month": 9, "day": 12}, "dueTime": {"hours": 17, "minutes": 30}}
    )
    assert due == datetime(2026, 9, 12, 17, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "work", [{}, {"dueDate": {"year": 2026}}, {"dueDate": {"year": 2026, "month": 13, "day": 40}}]
)
def test_an_unparseable_classroom_deadline_is_absent_not_wrong(work):
    assert coursework_due_at(work) is None


def test_coursework_carries_the_course_it_came_from():
    item = normalize_coursework(
        {
            "id": "w1",
            "courseId": "c1",
            "title": "Essay",
            "dueDate": {"year": 2026, "month": 9, "day": 1},
        },
        course_name="CS401",
    )
    assert item.object_id == "c1:w1"
    assert item.author == "CS401"
    assert item.kind == "coursework"


# ── Canvas (5.3) ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "http://canvas.instructure.com",
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1",
        "https://localhost",
        "https://10.0.0.5",
        "file:///etc/passwd",
    ],
)
def test_a_canvas_host_that_points_inside_the_network_is_refused(url):
    """A connector base URL is configuration, which makes it a request-forgery vector."""
    with pytest.raises(ValueError, match="Canvas base URL"):
        _checked_base_url(url)


def test_a_real_canvas_host_is_accepted_and_normalized():
    assert _checked_base_url("https://canvas.instructure.com/") == "https://canvas.instructure.com"


def test_a_canvas_assignment_becomes_a_dated_sync_item():
    item = normalize_assignment(
        {
            "id": 55,
            "course_id": 7,
            "name": "Lab 4",
            "due_at": "2026-09-12T17:30:00Z",
            "html_url": "https://canvas.instructure.com/courses/7/assignments/55",
        },
        course_name="Physics",
    )
    assert item.object_id == "7:55"
    assert item.occurred_at == datetime(2026, 9, 12, 17, 30, tzinfo=UTC)
    assert item.author == "Physics"


def test_a_canvas_assignment_without_a_due_date_has_none():
    assert (
        normalize_assignment({"id": 1, "name": "Ungraded survey", "due_at": None}).occurred_at
        is None
    )


# ── WhatsApp (5.4) ─────────────────────────────────────────────────────
def test_the_whatsapp_body_is_a_template_never_free_text():
    """Outside the 24h window Meta accepts only approved templates — and a fixed sentence
    with named parameters is also the shape an untrusted title cannot subvert."""
    payload = template_payload("+919999999999", title="Report", body="Due in 2 hours")
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "jarvis_deadline_alert"
    assert [p["text"] for p in payload["template"]["components"][0]["parameters"]] == [
        "Report",
        "Due in 2 hours",
    ]


def test_newlines_are_collapsed_because_meta_rejects_them():
    payload = template_payload("+91", title="Line\none\ttwo", body="a\n\nb")
    parameters = payload["template"]["components"][0]["parameters"]
    assert parameters[0]["text"] == "Line one two"
    assert parameters[1]["text"] == "a b"


# ── Twilio (5.5) ───────────────────────────────────────────────────────
def test_the_spoken_script_escapes_a_title_that_came_from_an_email():
    twiml = twiml_for("Submit <Say>hacked</Say> & go", "Due now")
    assert "<Say>hacked</Say>" not in twiml
    assert "&amp;" in twiml
    assert twiml.count("<Say") == 2  # ours, and the sign-off — nothing injected
