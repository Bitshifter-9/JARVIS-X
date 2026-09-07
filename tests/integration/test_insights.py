"""Mail intelligence: labels, spending, travel, away digest (FEATURES-50 #32/36/37/40)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jarvis.db.models.source import SourceObject
from jarvis.services.identity import IdentityService
from jarvis.services.insights import (
    away_digest,
    derive_insights,
    label_for,
    spending_from,
    travel_from,
)

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_label_for_reads_the_obvious_cues():
    assert label_for("Your Amazon order has shipped", "ship@amazon.com", "") == "Shopping"
    assert label_for("Invoice #22 due", "billing@razorpay.com", "amount due") == "Finance"
    assert label_for("Boarding pass", "no-reply@indigo.in", "PNR ABC123") == "Travel"
    assert label_for("Assignment 3 posted", "canvas@instructure.com", "") == "School"
    assert label_for("hey", "friend@example.com", "lunch?") == "Other"


def test_spending_only_fires_on_a_money_cue():
    got = spending_from("Receipt", "billing@swiggy.com", "Grand total: ₹1,299.00 paid")
    assert got == {"amount": 1299.0, "currency": "INR", "merchant": "Swiggy", "is_bill": False}

    bill = spending_from("Electricity bill", "no-reply@bescom.in", "Amount due $45.50")
    assert bill and bill["amount"] == 45.5 and bill["currency"] == "USD" and bill["is_bill"]

    # A promo with a dollar figure but no purchase cue must not count as spending.
    assert spending_from("$5 off your next order", "deals@shop.com", "grab $5 off now") is None


def test_travel_extracts_type_and_ref():
    got = travel_from(
        "Bengaluru to Delhi", "tickets@airindia.in", "Your flight PNR: XY12AB departs 5 Oct"
    )
    assert got and got["type"] == "flight" and got["ref"] == "XY12AB"
    assert "Bengaluru" in (got["where"] or "")

    hotel = travel_from("Reservation confirmed", "stay@hotel.com", "Check-in 12 Dec, 2 nights")
    assert hotel and hotel["type"] == "hotel"

    assert travel_from("lunch", "a@b.com", "see you at noon") is None


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("insights@example.com", PASSWORD)
    await session.commit()
    return u


def _email(user_id, oid, subject, sender, body, when):
    return SourceObject(
        user_id=user_id,
        provider="gmail",
        object_id=oid,
        kind="email",
        title=subject,
        author=sender,
        excerpt=body,
        occurred_at=when,
    )


async def test_derive_is_idempotent_and_feeds_the_digest(session, user):
    now = datetime.now(UTC)
    session.add_all(
        [
            _email(user.id, "m1", "Receipt", "billing@swiggy.com",
                   "Grand total: ₹250.00 paid", now),
            _email(user.id, "m2", "Electricity bill", "no-reply@bescom.in",
                   "Amount due ₹1,200.00", now),
            _email(user.id, "m3", "Bengaluru to Delhi", "tickets@indigo.in",
                   "Flight PNR: AB12CD, 5 Oct", now),
        ]
    )
    await session.flush()

    assert await derive_insights(session, user.id) == 3
    # Re-running writes nothing new.
    assert await derive_insights(session, user.id) == 0

    digest = await away_digest(session, user.id)
    assert digest["total"] == 3
    assert digest["by_label"].get("Finance") == 2
    assert digest["by_label"].get("Travel") == 1
    assert len(digest["bills"]) == 1 and digest["bills"][0]["amount"] == 1200.0
    assert len(digest["trips"]) == 1 and digest["trips"][0]["type"] == "flight"


async def test_the_endpoints(client, session):
    await IdentityService(session).register("ep@example.com", PASSWORD)
    await session.commit()
    r = await client.post("/v1/auth/login", json={"email": "ep@example.com", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from jarvis.db.models.identity import User
    from jarvis.db.session import get_sessionmaker
    from sqlalchemy import select

    async with get_sessionmaker()() as s:
        uid = (await s.scalars(select(User.id).where(User.email == "ep@example.com"))).one()
        s.add(_email(uid, "z1", "Receipt", "billing@zomato.com",
                     "Total charged ₹499.00", datetime.now(UTC)))
        await s.commit()

    scanned = (await client.post("/v1/insights/scan", headers=auth)).json()
    assert scanned == {"ok": True, "new": 1}

    spend = (await client.get("/v1/insights/spending", headers=auth)).json()
    assert spend["totals"].get("INR") == 499.0
    assert (await client.get("/v1/insights/labels", headers=auth)).json().get("Finance") == 1


async def test_grouped_activity_clusters_by_sender(session, user):
    now = datetime.now(UTC)
    for i in range(3):
        session.add(_email(user.id, f"a{i}", "hi", "boss@work.com", "x", now))
    session.add(_email(user.id, "b0", "promo", "deals@shop.com", "y", now))
    await session.flush()
    from jarvis.services.insights import grouped_activity

    clusters = await grouped_activity(session, user.id)
    top = clusters[0]
    assert top["who"] == "boss@work.com" and top["count"] == 3


async def test_anomaly_nudges_flag_a_spike(session, user):
    from datetime import timedelta

    from jarvis.services.insights import anomaly_nudges, derive_insights

    now = datetime.now(UTC)
    # baseline: one Finance mail on each of several earlier days
    for d in range(2, 8):
        session.add(_email(user.id, f"old{d}", "Invoice", "billing@razorpay.com",
                           "amount due ₹10", now - timedelta(days=d)))
    # today: a spike of Finance mail
    for i in range(5):
        session.add(_email(user.id, f"new{i}", "Invoice", "billing@razorpay.com",
                           "amount due ₹10", now))
    await session.flush()
    await derive_insights(session, user.id)

    nudges = await anomaly_nudges(session, user.id)
    assert any(n["label"] == "Finance" and n["today"] == 5 for n in nudges)
