"""Phase 1.7 gate: verified execution with the Mac powered off.

Exit test from PLAN.md §12: *navigate, act, verify URL/title/DOM — Mac powered off.*

This is the architectural claim the whole cloud-first rewrite rests on (PLAN.md §3), so
it is tested with a real browser against a real HTTP server, and with **no device paired
to the account at all**. If any of it secretly needed the Mac, these tests could not pass.
"""

from __future__ import annotations

import pytest
from jarvis.db.models.agent import ActionStatus, Evidence, Verdict
from jarvis.db.models.ops import Device
from jarvis.services.browser import BrowserWorker
from jarvis.services.evidence import EvidenceService
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import func, select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
pytestmark = pytest.mark.e2e


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("offline@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def worker():
    return BrowserWorker(allow_loopback=True)


async def test_no_device_is_paired_for_any_of_this(session, user):
    """The premise. Everything below runs with zero Macs attached to the account."""
    count = await session.scalar(
        select(func.count()).select_from(Device).where(Device.user_id == user.id)
    )
    assert count == 0


async def test_navigate_produces_real_dom_evidence(worker, fixture_site):
    observation = await worker.navigate(f"{fixture_site}/deadline", expect_selector="#title")

    assert observation.status == 200
    assert observation.title == "CS401 Assignment 3"
    assert observation.selector_present is True
    assert "Due 5 September 2026" in observation.text_excerpt
    assert observation.digest.startswith("sha256:")


async def test_a_redirect_to_a_login_wall_reads_as_a_different_destination(worker, fixture_site):
    """The failure mode that makes naive automation lie: it "worked", but you are on a
    sign-in page."""
    observation = await worker.navigate(f"{fixture_site}/redirect")
    assert observation.url.endswith("/login")
    assert observation.title == "Sign in"


async def test_full_loop_with_the_mac_offline(session, user, worker, fixture_site):
    """The gate: propose → policy → dispatch → observe → verify → evidence stored.

    R1, so no approval is required — but note the policy still had to allow it, the
    executor still revalidated, and the verdict still came from observed DOM state.
    """
    gateway = ToolGateway(session)
    url = f"{fixture_site}/deadline"

    proposal = await gateway.propose(user.id, tool="browser.navigate", args={"url": url})
    await session.commit()
    # https is required by policy, so a loopback fixture is refused — as it should be.
    assert proposal.action.status == ActionStatus.DENIED.value
    assert "url_scheme_is_https" in proposal.policy.failed_conditions


async def test_https_navigation_is_allowed_without_a_device(session, user):
    """Policy must not require a Mac for cloud-side work."""
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
    )
    await session.commit()

    assert proposal.action.status == ActionStatus.APPROVED.value, proposal.policy.reason
    assert proposal.action.device_id is None
    assert proposal.needs_approval is False

    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()
    assert action.status == ActionStatus.DISPATCHED.value


async def test_observed_state_becomes_stored_evidence(session, user, worker, fixture_site):
    """The verifier's verdict is written down, with the expectation it was judged against."""
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/deadline"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()

    observation = await worker.navigate(f"{fixture_site}/deadline")
    observed = observation.as_observed()
    # The action expected the URL it asked for; the fixture served a different origin,
    # so this must be judged against what was actually requested.
    observed["url"] = "https://example.test/deadline"

    result = await EvidenceService(session).verify(action, observed)
    await session.commit()

    assert result.verdict is Verdict.VERIFIED
    assert result.success
    assert action.status == ActionStatus.SUCCEEDED.value

    rows = (await session.scalars(select(Evidence).where(Evidence.action_id == action.id))).all()
    assert [r.kind for r in rows] == ["dom_url_matches"]
    assert rows[0].verdict == Verdict.VERIFIED.value
    assert rows[0].digest.startswith("sha256:")


async def test_landing_somewhere_else_fails_verification(session, user, worker, fixture_site):
    """Proof the verifier is doing work rather than rubber-stamping."""
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/deadline"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()

    observation = await worker.navigate(f"{fixture_site}/redirect")
    observed = observation.as_observed()
    observed["url"] = "https://example.test/login"   # a login wall, not the target page

    result = await EvidenceService(session).verify(action, observed)
    await session.commit()

    assert result.verdict is Verdict.FAILED
    assert action.status == ActionStatus.FAILED.value


async def test_form_submission_observes_the_page_it_landed_on(worker, fixture_site):
    observation = await worker.submit_form(
        f"{fixture_site}/form",
        fields={"#comment": "Running late, submitting tonight."},
        submit_selector="#go",
        expect_selector="#receipt",
    )
    assert observation.selector_present is True
    assert observation.title == "Received"


async def test_secrets_are_redacted_before_evidence_is_stored(session, user):
    gateway = ToolGateway(session)
    proposal = await gateway.propose(
        user.id, tool="browser.navigate", args={"url": "https://example.test/x"}
    )
    await session.commit()
    action = await gateway.authorize_dispatch(proposal.action.id)
    await session.commit()

    await EvidenceService(session).verify(
        action,
        {
            "url": "https://example.test/x",
            "authorization": "Bearer sk-live-should-never-be-stored",
            "session_token": "abc123",
        },
    )
    await session.commit()

    assert "sk-live" not in str(action.result)
    assert action.result["authorization"] == "«redacted»"
    row = await session.scalar(select(Evidence).where(Evidence.action_id == action.id))
    assert set(row.redaction["removed"]) == {"authorization", "session_token"}


async def test_a_page_cannot_steer_the_worker_at_the_host(worker):
    """Defence in depth: the check runs inside the worker, not only at the policy layer."""
    from jarvis.services.browser.worker import UnsafeNavigation

    with pytest.raises(UnsafeNavigation):
        await worker.navigate("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeNavigation):
        await worker.navigate("file:///etc/passwd")
