"""Heuristic mail readers plus the store/query layer.

The readers are pure functions so they are unit-tested without a database. They are
deliberately conservative: a wrong label is annoying, a wrong charge on the spending
total is worse, so `spending_from` fires only on an explicit money cue.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.db.models.source import MailInsight, SourceObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ── labels ────────────────────────────────────────────────────────────────
# First keyword or sender-domain hit wins, in this order.
_LABEL_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("Travel", ("flight", "boarding", "itinerary", "pnr", "check-in", "hotel",
                "reservation", "booking.com", "airbnb", "irctc", "trip"),
     ("makemytrip", "booking.com", "airbnb", "expedia", "indigo", "airindia", "irctc")),
    ("Finance", ("invoice", "receipt", "payment", "paid", "transaction", "statement",
                 "bill", "due", "charged", "refund", "order total", "gst"),
     ("razorpay", "stripe", "paypal", "phonepe", "gpay", "hdfc", "icici", "sbi", "amazonpay")),
    ("Shopping", ("your order", "shipped", "delivered", "dispatched", "out for delivery",
                  "tracking"),
     ("amazon", "flipkart", "myntra", "swiggy", "zomato", "ebay")),
    ("School", ("assignment", "coursework", "exam", "semester", "canvas", "classroom",
                "submission", "grade", "lecture", "professor"),
     ("instructure", "canvas", "classroom.google")),
    ("Work", ("meeting", "standup", "deadline", "project", "review", "sprint", "jira",
              "pull request", "deploy", "1:1"),
     ("slack", "atlassian", "github", "linear", "notion")),
    ("Social", ("commented", "tagged", "mentioned you", "friend request", "new follower",
                "liked your"),
     ("facebook", "instagram", "twitter", "x.com", "linkedin", "reddit")),
    ("Newsletter", ("newsletter", "unsubscribe", "weekly digest", "this week in",
                    "read more"),
     ("substack", "mailchimp", "beehiiv")),
]


# Match keywords on word boundaries, so "exam" does not fire inside "example.com".
_LABEL_KEYWORD_RE = [
    (label, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b"), domains)
    for label, keywords, domains in _LABEL_RULES
]


def label_for(subject: str, sender: str, body: str) -> str:
    hay = f"{subject}\n{body}".lower()
    sender_low = sender.lower()
    for label, keyword_re, domains in _LABEL_KEYWORD_RE:
        if any(d in sender_low for d in domains):
            return label
        if keyword_re.search(hay):
            return label
    return "Other"


# ── spending ──────────────────────────────────────────────────────────────
_CURRENCY = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
}
# An amount that sits next to a money word — "total: ₹1,299.00", "you paid $12".
_MONEY_CUE = (
    "total", "paid", "amount", "charged", "payment", "invoice", "receipt",
    "bill", "due", "grand total", "order total", "subtotal", "debited",
)
_BILL_CUE = ("due", "invoice", "statement", "bill", "outstanding", "pay now", "auto-pay")
_AMOUNT = re.compile(
    r"(₹|rs\.?|inr|\$|usd|€|eur|£|gbp)\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)


def spending_from(subject: str, sender: str, body: str) -> dict[str, Any] | None:
    """The charge an email describes, or None. Only fires when an amount sits in text
    that also carries a money cue, so a "$5 off" promo does not become a purchase."""
    text = f"{subject}\n{body}"
    low = text.lower()
    if not any(cue in low for cue in _MONEY_CUE):
        return None
    best: tuple[float, str] | None = None
    for m in _AMOUNT.finditer(text):
        # Require a money cue within ~40 chars of the amount.
        window = low[max(0, m.start() - 40): m.end() + 10]
        if not any(cue in window for cue in _MONEY_CUE):
            continue
        try:
            value = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        code = _CURRENCY.get(m.group(1).lower().rstrip("."), m.group(1).upper())
        # Keep the largest cued amount — usually the grand total, not a line item.
        if best is None or value > best[0]:
            best = (value, code)
    if best is None:
        return None
    merchant = _merchant(sender)
    return {
        "amount": best[0],
        "currency": best[1],
        "merchant": merchant,
        "is_bill": any(cue in low for cue in _BILL_CUE),
    }


def _merchant(sender: str) -> str | None:
    m = re.search(r"@([\w.-]+)", sender)
    if m:
        host = m.group(1).split(".")
        # drop a leading mail/noreply subdomain and the TLD
        parts = [p for p in host if p not in ("com", "co", "in", "net", "org", "email",
                                              "mail", "noreply", "no-reply")]
        if parts:
            return parts[-1].capitalize()
    name = sender.split("<")[0].strip().strip('"')
    return name[:200] or None


# ── travel ────────────────────────────────────────────────────────────────
_FLIGHT_CUE = ("flight", "boarding pass", "pnr", "departure", "gate", "e-ticket", "airline")
_HOTEL_CUE = ("hotel", "check-in", "check in", "reservation", "room", "nights", "stay")
_PNR = re.compile(r"\b(?:pnr|booking(?: ref| reference)?|confirmation)[:\s#]+([A-Z0-9]{5,8})\b",
                  re.IGNORECASE)


def travel_from(subject: str, sender: str, body: str) -> dict[str, Any] | None:
    """A flight/hotel confirmation reduced to {type, when, where, ref}, or None."""
    low = f"{subject}\n{body}".lower()
    is_flight = any(c in low for c in _FLIGHT_CUE)
    is_hotel = any(c in low for c in _HOTEL_CUE)
    if not (is_flight or is_hotel):
        return None
    ref = None
    m = _PNR.search(f"{subject}\n{body}")
    if m:
        ref = m.group(1).upper()
    when = _first_date(body) or _first_date(subject)
    return {
        "type": "flight" if is_flight else "hotel",
        "when": when,
        "where": _route_or_place(subject, is_flight),
        "ref": ref,
    }


_DATE = re.compile(
    r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{4})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b",
    re.IGNORECASE,
)


def _first_date(text: str) -> str | None:
    m = _DATE.search(text)
    return m.group(1) if m else None


def _route_or_place(subject: str, is_flight: bool) -> str | None:
    # "Bengaluru → Delhi" / "Bengaluru to Delhi" in the subject, else the subject itself.
    m = re.search(r"([A-Z][\w. ]+?)\s*(?:→|->|to|-)\s*([A-Z][\w. ]+)", subject)
    if m and is_flight:
        return f"{m.group(1).strip()} → {m.group(2).strip()}"
    return subject.strip()[:120] or None


# ── store / query ─────────────────────────────────────────────────────────
async def derive_insights(session: AsyncSession, user_id: uuid.UUID, *, limit: int = 200) -> int:
    """Read every recent email that has no insight yet; write one each. Returns how many
    new rows were written."""
    seen = set(
        (
            await session.scalars(
                select(MailInsight.source_id).where(MailInsight.user_id == user_id)
            )
        ).all()
    )
    rows = (
        await session.scalars(
            select(SourceObject)
            .where(
                SourceObject.user_id == user_id,
                SourceObject.provider.in_(("gmail", "slack")),
                SourceObject.kind == "email",
            )
            .order_by(SourceObject.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    written = 0
    for obj in rows:
        if obj.id in seen:
            continue
        subject = obj.title or ""
        sender = obj.author or ""
        body = obj.excerpt or ((obj.raw or {}).get("body") if obj.raw else "") or ""
        spend = spending_from(subject, sender, body)
        travel = travel_from(subject, sender, body)
        session.add(
            MailInsight(
                user_id=user_id,
                source_id=obj.id,
                occurred_at=obj.occurred_at,
                label=label_for(subject, sender, body),
                amount=spend["amount"] if spend else None,
                currency=spend["currency"] if spend else None,
                merchant=spend["merchant"] if spend else None,
                is_bill=bool(spend and spend["is_bill"]),
                travel=travel,
            )
        )
        written += 1
    await session.flush()
    return written


async def away_digest(
    session: AsyncSession, user_id: uuid.UUID, *, since: datetime | None = None
) -> dict[str, Any]:
    """"What changed while I was away": new mail since ``since`` grouped by label, plus
    any bills due and travel found. Defaults to the last 24 hours."""
    since = since or datetime.now(UTC) - timedelta(hours=24)
    rows = (
        await session.scalars(
            select(MailInsight)
            .where(MailInsight.user_id == user_id, MailInsight.occurred_at >= since)
            .order_by(MailInsight.occurred_at.desc())
        )
    ).all()
    by_label: dict[str, int] = defaultdict(int)
    bills: list[dict[str, Any]] = []
    trips: list[dict[str, Any]] = []
    for r in rows:
        by_label[r.label] += 1
        if r.is_bill and r.amount is not None:
            bills.append({"merchant": r.merchant, "amount": r.amount, "currency": r.currency})
        if r.travel:
            trips.append(r.travel)
    return {
        "since": since.isoformat(),
        "total": len(rows),
        "by_label": dict(by_label),
        "bills": bills,
        "trips": trips,
    }
