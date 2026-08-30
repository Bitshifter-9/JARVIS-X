"""Phase 6.3 gate: metrics measured, not asserted.

Exit test from PLAN.md §12: all seven targets measured from what actually happened.

The most important behaviour here is what happens with no data: a metric with no
observations must report "not yet measured", never a flattering 100%.
"""

from __future__ import annotations

import pytest
from jarvis.db.models.ops import NotificationEndpoint  # noqa: F401  (schema import)
from jarvis.services.identity import IdentityService
from jarvis.services.metrics import MetricsService
from jarvis.services.tool_gateway import ToolGateway

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
SEND_ARGS = {"channel": "telegram", "to": "@team", "body": "hi"}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("metrics@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def metrics(session):
    return MetricsService(session)


async def test_an_empty_system_reports_nothing_measured_not_success(session, user, metrics):
    """"We have not measured this" and "this is at 100%" are very different statements."""
    card = await metrics.scorecard(user.id)

    quality = [m for m in card.metrics if m.key != "monthly_cost"]
    assert all(m.value is None for m in quality)
    assert all(m.met is None for m in quality)
    assert all(m.display == "not yet measured" for m in quality)
    assert "not yet measured" in card.headline


async def test_all_seven_targets_are_present(session, user, metrics):
    card = await metrics.scorecard(user.id)
    assert {m.key for m in card.metrics} == {
        "duplicate_task_rate",
        "verified_tool_success",
        "approval_coverage",
        "event_to_alert_latency",
        "recovery_correctness",
        "injection_block_rate",
        "monthly_cost",
    }


async def test_verified_tool_success_is_computed_from_evidence(session, user, metrics):
    from jarvis.services.evidence import EvidenceService

    gateway = ToolGateway(session)
    for landed in ["https://example.test/x", "https://example.test/x", "https://evil.test/y"]:
        proposal = await gateway.propose(
            user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
        )
        await session.commit()
        action = await gateway.authorize_dispatch(proposal.action.id)
        await session.commit()
        await EvidenceService(session).verify(action, {"url": landed})
        await session.commit()

    card = await metrics.scorecard(user.id)
    metric = next(m for m in card.metrics if m.key == "verified_tool_success")
    assert metric.sample_size == 3
    assert metric.value == pytest.approx(2 / 3)
    assert metric.met is False, "two of three is below the 95% target"


async def test_approval_coverage_counts_only_actions_that_actually_ran(session, user, metrics):
    """A denied proposal never needed an approval; counting it would flatter the number."""
    gateway = ToolGateway(session)

    approved = await gateway.propose(user.id, tool="message.send", args=SEND_ARGS)
    await session.commit()
    await gateway.decide(user.id, approved.approval.id, approved=True, decided_by="mobile")
    await session.commit()
    await gateway.authorize_dispatch(approved.action.id)
    await session.commit()

    # A denied R4 proposal, which is never dispatched.
    await gateway.propose(user.id, tool="payment.send", args={"amount": 1})
    await session.commit()

    card = await metrics.scorecard(user.id)
    metric = next(m for m in card.metrics if m.key == "approval_coverage")
    assert metric.sample_size == 1
    assert metric.value == 1.0
    assert metric.met is True


async def test_bounded_recovery_is_measured_from_attempts(session, user, metrics):
    from jarvis.db.queue import JobQueue
    from sqlalchemy import text

    q = JobQueue(session)
    good = await q.enqueue("m.good", {}, user_id=user.id)
    await session.commit()
    await q.claim("w", limit=1)
    await q.complete(good.id)
    await session.commit()

    bad = await q.enqueue("m.bad", {}, user_id=user.id, max_attempts=5)
    await session.commit()
    for _ in range(5):
        claimed = await q.claim("w", limit=1)
        await session.commit()
        if not claimed:
            break
        await q.fail(bad.id, "broken")
        await session.commit()
        await session.execute(
            text("UPDATE jobs SET visible_at = clock_timestamp() - interval '1s' "
                 "WHERE id = :i AND status='pending'"),
            {"i": bad.id},
        )
        await session.commit()

    card = await metrics.scorecard(user.id)
    metric = next(m for m in card.metrics if m.key == "recovery_correctness")
    assert metric.sample_size == 2
    assert metric.value == pytest.approx(0.5), "one bounded, one that burned its attempts"


async def test_injection_blocks_are_measured_from_the_audit_log(session, user, metrics):
    gateway = ToolGateway(session)
    for _ in range(4):
        await gateway.propose(
            user.id, tool="message.send", args=SEND_ARGS, from_untrusted_source=True
        )
        await session.commit()

    card = await metrics.scorecard(user.id)
    metric = next(m for m in card.metrics if m.key == "injection_block_rate")
    assert metric.sample_size == 4
    assert metric.value == 1.0
    assert metric.met is True


async def test_cost_is_measured_for_the_calendar_month(session, user, metrics):
    from jarvis.core.ids import uuid7
    from sqlalchemy import text

    for cost in [1.5, 2.25]:
        await session.execute(
            text("""
                INSERT INTO llm_calls (id, user_id, call_class, provider, model,
                    input_tokens, output_tokens, cost_inr, latency_ms, status, attempt,
                    created_at, updated_at)
                VALUES (:id, :uid, 'extract', 'openrouter_paid', 'm', 100, 50, :c, 10, 'ok', 1,
                    now(), now())
            """),
            {"id": uuid7(), "uid": user.id, "c": cost},
        )
    await session.commit()

    card = await metrics.scorecard(user.id)
    metric = next(m for m in card.metrics if m.key == "monthly_cost")
    assert metric.value == pytest.approx(3.75)
    assert metric.display == "₹3.75"
    assert metric.met is True


async def test_metrics_are_scoped_to_their_owner(session, user, metrics):
    from jarvis.services.evidence import EvidenceService

    other = await IdentityService(session).register("other@example.com", PASSWORD)
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    await EvidenceService(session).verify(action, {"url": "https://example.test/x"})
    await session.commit()

    theirs = await metrics.scorecard(other.id)
    metric = next(m for m in theirs.metrics if m.key == "verified_tool_success")
    assert metric.sample_size == 0
    assert metric.value is None


async def test_the_window_bounds_what_is_counted(session, user, metrics):
    from jarvis.services.evidence import EvidenceService
    from sqlalchemy import text

    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    await EvidenceService(session).verify(action, {"url": "https://example.test/x"})
    await session.commit()

    await session.execute(text("UPDATE evidence SET created_at = now() - interval '30 days'"))
    await session.commit()

    recent = await metrics.scorecard(user.id, window_days=7)
    assert next(m for m in recent.metrics if m.key == "verified_tool_success").sample_size == 0

    wide = await metrics.scorecard(user.id, window_days=60)
    assert next(m for m in wide.metrics if m.key == "verified_tool_success").sample_size == 1


async def test_the_scorecard_is_reachable_over_http(client):
    await client.post(
        "/v1/auth/register", json={"email": "m@example.com", "password": PASSWORD}
    )
    tokens = (
        await client.post(
            "/v1/auth/login", json={"email": "m@example.com", "password": PASSWORD}
        )
    ).json()
    response = await client.get(
        "/v1/metrics", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["metrics"]) == 7
    assert "headline" in body


async def test_a_failing_metric_is_reported_as_failing(session, user, metrics):
    from jarvis.services.evidence import EvidenceService

    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    await EvidenceService(session).verify(action, {"url": "https://evil.test/y"})
    await session.commit()

    card = await metrics.scorecard(user.id)
    assert card.failing, "a missed target must show as failing, not be rounded away"
    assert any(m.key == "verified_tool_success" for m in card.failing)
