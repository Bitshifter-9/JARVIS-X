"""A no-model deadline reader, for when the LLM cascade is exhausted.

Free-tier LLM quota runs out; when it does, a mail that plainly says "due 9 September at
9 am" should still become a task. This catches the obvious phrasings — a date, optionally
a time, near a deadline word — and nothing clever. It is deliberately conservative
(confidence 0.55, always flagged for confirmation) so a real model still wins when it can.
"""

from __future__ import annotations

import re
from datetime import datetime

from jarvis.services.extraction.schema import ExtractedDeadline

_MONTHS = {
    m: i
    for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
_CUE = re.compile(
    r"\b(due|deadline|submit|by|before|last date|expires?|exam|assignment|payment|"
    r"pay|rent|meeting|interview|appointment)\b",
    re.I,
)
# "9 September 2026", "September 9, 2026", "9 Sept", "09/09/2026", "2026-09-09"
_MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE = re.compile(
    r"""(?xi)
    (?:
      (?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon1>MONTHS)\.?(?:,?\s+(?P<y1>\d{4}))?
      | (?P<mon2>MONTHS)\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y2>\d{4}))?
      | (?P<iso>\d{4}-\d{2}-\d{2})
      | (?P<dmy>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})
    )
    """.replace("MONTHS", _MONTH_NAMES)
)
_WEEKDAYS = {
    d: i for i, d in enumerate(
        "monday tuesday wednesday thursday friday saturday sunday".split()
    )
}
_WEEKDAY = re.compile(
    r"\b(?P<rel>today|tomorrow|tonight|"
    r"(?:next\s+)?(?:mon|tues?|wed(?:nes)?|thurs?|fri|sat(?:ur)?|sun)(?:day)?)\b",
    re.I,
)
_TIME = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)\b", re.I)
_TIME24 = re.compile(r"\b(?P<h>[01]?\d|2[0-3]):(?P<m>\d{2})\b")


def _month(token: str | None) -> int | None:
    return _MONTHS.get(token[:3].lower()) if token else None


def _relative_date(text: str, received_at: datetime):  # noqa: ANN201
    from datetime import timedelta

    w = _WEEKDAY.search(text)
    if w is None:
        return None
    word = w.group("rel").lower().replace("next", "").strip()
    base = received_at
    if word in ("today", "tonight"):
        return datetime(base.year, base.month, base.day)
    if word == "tomorrow":
        d = base + timedelta(days=1)
        return datetime(d.year, d.month, d.day)
    # a weekday name → the next such day (or a week out if "next" was said)
    short = word[:3]
    target = next((i for name, i in _WEEKDAYS.items() if name.startswith(short)), None)
    if target is None:
        return None
    ahead = (target - base.weekday()) % 7
    if ahead == 0:
        ahead = 7
    if "next" in w.group("rel").lower():
        ahead += 7 if ahead <= 7 else 0
    d = base + timedelta(days=ahead)
    return datetime(d.year, d.month, d.day)


def extract_deadline(
    body: str, subject: str, received_at: datetime, *, require_cue: bool = True
) -> ExtractedDeadline | None:
    text = f"{subject}\n{body}"
    # Mail needs a deadline cue word to avoid false positives; a task the user typed by
    # hand (quick-add) does not — "call mom tomorrow" is already an intent.
    if require_cue and not _CUE.search(text) and not _WEEKDAY.search(text):
        return None
    year = received_at.year
    m = _DATE.search(text)
    date = None
    if m is None:
        # No explicit date — try a weekday or today/tomorrow/tonight.
        date = _relative_date(text, received_at)
        if date is None:
            return None
    try:
        if m is None:
            pass  # already resolved by _relative_date
        elif m.group("iso"):
            date = datetime.fromisoformat(m.group("iso"))
        elif m.group("dmy"):
            parts = [int(p) for p in re.split(r"[/-]", m.group("dmy"))]
            a, b = parts[0], parts[1]
            yr = parts[2] if len(parts) > 2 else year
            # Day-first (DD/MM), the norm where the owner is; swap only when it cannot be.
            day, month = (a, b) if a > 12 or b <= 12 else (b, a)
            date = datetime(yr if yr > 99 else 2000 + yr, month, day)
        elif m is not None:
            day = int(m.group("d1") or m.group("d2"))
            month = _month(m.group("mon1") or m.group("mon2"))
            if month is None:
                return None
            yr = int(m.group("y1") or m.group("y2") or year)
            date = datetime(yr, month, day)
    except (ValueError, TypeError):
        return None
    if date is None:
        return None

    # A bare date already in the past for this year probably means next year.
    hour, minute, all_day = 23, 59, True
    if "tonight" in text.lower():
        hour, minute, all_day = 20, 0, False
    if t := (_TIME.search(text) or _TIME24.search(text)):
        all_day = False
        hour = int(t.group("h"))
        minute = int(t.groupdict().get("m") or 0)
        if (ap := t.groupdict().get("ap")):
            if ap.lower() == "pm" and hour != 12:
                hour += 12
            elif ap.lower() == "am" and hour == 12:
                hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None

    span = (
        text[max(0, m.start() - 25) : m.end() + 20].strip().replace("\n", " ")
        if m is not None
        else text.strip()[:120]
    )
    title = (subject.strip() or "Deadline")[:300]
    return ExtractedDeadline(
        has_deadline=True,
        title=title,
        due_at_local=f"{date.year:04d}-{date.month:02d}-{date.day:02d}T{hour:02d}:{minute:02d}",
        all_day=all_day,
        confidence=0.55,
        kind="other",
        evidence_span=span[:500],
        ambiguity="read without a model (LLM quota exhausted); confirm the date",
    )
