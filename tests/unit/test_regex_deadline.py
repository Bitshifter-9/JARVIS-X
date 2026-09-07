"""The no-model deadline reader (PLAN.md 12.7): catch obvious dates, refuse the rest."""

from datetime import UTC, datetime

import pytest
from jarvis.services.extraction.regex_fallback import extract_deadline

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("subject", "body", "due", "all_day"),
    [
        ("Meeting", "exam deadline on 9 September 2026 at 9 am", "2026-09-09T09:00", False),
        ("Rent", "Rent is due September 30, 2026", "2026-09-30T23:59", True),
        ("Lab", "Submit before 05/10/2026 by 6pm", "2026-10-05T18:00", False),
        ("Sync", "The meeting is on 2026-09-15 at 14:30", "2026-09-15T14:30", False),
        ("Assignment 3", "Due 5 Sept at 11:59 pm", "2026-09-05T23:59", False),
    ],
)
def test_it_reads_obvious_deadlines(subject, body, due, all_day):
    d = extract_deadline(body, subject, NOW)
    assert d is not None and d.has_deadline
    assert d.due_at_local == due
    assert d.all_day is all_day
    assert d.confidence == 0.55 and d.evidence_span
    assert d.title == subject


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        ("Lunch?", "Want to grab lunch sometime next week?"),   # a cue word but no date
        ("Newsletter", "Read how Monza unfolded this weekend"),  # no cue, no date
        ("Hi", "Talk soon"),
        ("Sale", "50% off everything"),
    ],
)
def test_it_refuses_when_there_is_no_clear_date(subject, body):
    assert extract_deadline(body, subject, NOW) is None


@pytest.mark.parametrize(
    ("text", "weekday", "hour"),
    [
        ("pay rent friday 6pm", 4, 18),   # Friday = weekday 4
        ("call mom tomorrow", None, 23),  # tomorrow, no time → end of day
        ("submit tonight", None, 20),     # tonight → 8pm
    ],
)
def test_relative_days(text, weekday, hour):
    d = extract_deadline(text, text, NOW, require_cue=False)
    assert d is not None and d.has_deadline
    parsed = datetime.fromisoformat(d.due_at_local)
    assert parsed.hour == hour
    if weekday is not None:
        assert parsed.weekday() == weekday
        assert parsed > NOW.replace(tzinfo=None)
