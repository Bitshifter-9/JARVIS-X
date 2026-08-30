"""Gmail normalization: the part that decides what the extractor even sees."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from jarvis.connectors.google.calendar import merged_minutes, normalize_event
from jarvis.connectors.google.gmail import clean_body, normalize


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _message(*, subject="Subject", plain=None, html=None, sender="prof@uni.edu", parts=None):
    payload = {
        "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}],
        "mimeType": "multipart/alternative" if parts else ("text/html" if html else "text/plain"),
        "body": {"data": _b64(plain or html or "")} if not parts else {},
    }
    if parts:
        payload["parts"] = parts
    return {"id": "18c9f", "threadId": "t1", "internalDate": "1788000000000", "payload": payload}


def test_a_plain_message_normalizes():
    item = normalize(
        _message(subject="CS401 Assignment 3", plain="Due 5 September 2026.")
    )
    assert item.provider == "gmail"
    assert item.object_id == "18c9f"
    assert item.title == "CS401 Assignment 3"
    assert "5 September 2026" in item.body
    assert item.occurred_at == datetime.fromtimestamp(1788000000, tz=UTC)


def test_html_is_reduced_to_text():
    item = normalize(
        _message(html="<html><body><p>Due <b>7 Sept 2026</b> at 6pm</p></body></html>")
    )
    assert "<" not in item.body
    assert "7 Sept 2026" in item.body


def test_nested_multipart_prefers_plain_text():
    parts = [
        {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
        {
            "mimeType": "multipart/related",
            "parts": [{"mimeType": "text/plain", "body": {"data": _b64("plain version")}}],
        },
    ]
    item = normalize(_message(parts=parts))
    assert item.body == "plain version"


def test_quoted_history_is_stripped():
    """Quoted replies carry superseded deadlines; leaving them in extracts the wrong date."""
    body = clean_body(
        "New deadline is 10 Sept 2026, 3pm.\n\n"
        "> On 20 Aug, prof wrote:\n> Old deadline 25 Aug 2026.",
        "",
    )
    assert "10 Sept 2026" in body
    assert "25 Aug 2026" not in body


def test_signatures_are_stripped():
    body = clean_body("Report due 9 Sep 2026.\n-- \nDr Sharma\nSent from my iPhone", "")
    assert "9 Sep 2026" in body
    assert "iPhone" not in body


def test_entities_are_decoded():
    assert "Assignment 2" in clean_body("", "Assignment&nbsp;2 due soon")


def test_bodies_are_capped():
    item = normalize(_message(plain="x" * 50_000))
    assert len(item.body) <= 12_000


# ── calendar ───────────────────────────────────────────────────────────
def test_overlapping_meetings_are_not_counted_twice():
    """Double counting makes forecasts pessimistic exactly when the user is busiest."""
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    end = datetime(2026, 8, 30, 17, tzinfo=UTC)
    spans = [
        (datetime(2026, 8, 30, 10, tzinfo=UTC), datetime(2026, 8, 30, 12, tzinfo=UTC)),
        (datetime(2026, 8, 30, 11, tzinfo=UTC), datetime(2026, 8, 30, 13, tzinfo=UTC)),
    ]
    assert merged_minutes(spans, start, end) == 180


def test_busy_spans_are_clipped_to_the_window():
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    end = datetime(2026, 8, 30, 13, tzinfo=UTC)
    spans = [(datetime(2026, 8, 30, 9, tzinfo=UTC), datetime(2026, 8, 30, 18, tzinfo=UTC))]
    assert merged_minutes(spans, start, end) == 60


def test_disjoint_meetings_add_up():
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    end = datetime(2026, 8, 30, 17, tzinfo=UTC)
    spans = [
        (datetime(2026, 8, 30, 9, tzinfo=UTC), datetime(2026, 8, 30, 10, tzinfo=UTC)),
        (datetime(2026, 8, 30, 14, tzinfo=UTC), datetime(2026, 8, 30, 15, 30, tzinfo=UTC)),
    ]
    assert merged_minutes(spans, start, end) == 150


def test_no_meetings_is_no_busy_time():
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    assert merged_minutes([], start, start.replace(hour=17)) == 0


@pytest.mark.parametrize("all_day", [True, False])
def test_events_normalize_with_and_without_times(all_day):
    event = {
        "id": "e1",
        "summary": "Lecture",
        "start": {"date": "2026-09-05"} if all_day else {"dateTime": "2026-09-05T10:00:00+05:30"},
        "end": {"date": "2026-09-06"} if all_day else {"dateTime": "2026-09-05T11:00:00+05:30"},
    }
    item = normalize_event(event)
    assert item.title == "Lecture"
    assert item.occurred_at is not None
    assert item.raw["all_day"] is all_day
