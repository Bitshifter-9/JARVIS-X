"""The verifier: evidence, not exit codes.

The rule the architecture rests on (blueprint §2) — an action succeeded when the world is
observably in the expected state, and not before.
"""

from __future__ import annotations

import pytest
from jarvis.db.models.agent import Verdict
from jarvis.services.browser.worker import UnsafeNavigation, assert_safe_url
from jarvis.services.evidence.verifier import (
    EvidenceRequirement,
    Verifier,
    check_requirement,
)


def _check(kind, observed, value=None):
    return check_requirement(EvidenceRequirement(kind, value), observed)


# ── the central rule ───────────────────────────────────────────────────
def test_a_zero_exit_code_is_not_evidence():
    """A tool that returned success but changed nothing has failed."""
    result = _check("foreground_window_bundle_id", {"exit_code": 0}, "com.google.Chrome")
    assert result.verdict is Verdict.INCONCLUSIVE
    assert not result.passed


def test_frontmost_window_is_evidence():
    ok = _check(
        "foreground_window_bundle_id",
        {"frontmost_bundle_id": "com.google.Chrome"},
        "com.google.Chrome",
    )
    assert ok.verdict is Verdict.VERIFIED

    wrong = _check(
        "foreground_window_bundle_id",
        {"frontmost_bundle_id": "com.apple.Safari"},
        "com.google.Chrome",
    )
    assert wrong.verdict is Verdict.FAILED
    assert "Safari is frontmost" in wrong.detail


def test_absent_observation_is_inconclusive_not_failed():
    """The agent repairs on new evidence and stops on none, so this distinction decides
    whether it looks again or gives up."""
    assert _check("process_running", {}).verdict is Verdict.INCONCLUSIVE
    assert _check("dom_url_matches", {}, "https://x.test").verdict is Verdict.INCONCLUSIVE


def test_a_missing_provider_object_id_is_a_failure_not_an_absence():
    """"The API returned 200" and "the message exists" are different claims."""
    result = _check("provider_object_id", {"status": 200})
    assert result.verdict is Verdict.FAILED
    assert "nothing is known to exist" in result.detail

    found = _check("provider_object_id", {"provider_object_id": "msg_9f2"})
    assert found.verdict is Verdict.VERIFIED


def test_unknown_evidence_kind_fails_closed():
    """A typo in a manifest must block the action, not silently approve it."""
    result = _check("teleport_confirmed", {"anything": True})
    assert result.verdict is Verdict.FAILED
    assert "unknown evidence kind" in result.detail


# ── URL comparison ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("https://example.test/page", "https://example.test/page/"),
        ("https://example.test/page", "https://www.example.test/page"),
        ("https://example.test", "https://example.test/"),
    ],
)
def test_cosmetic_url_differences_are_not_a_different_page(expected, actual):
    assert _check("dom_url_matches", {"url": actual}, expected).verdict is Verdict.VERIFIED


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("https://example.test/page", "https://evil.test/page"),
        ("https://example.test/page", "https://example.test/login"),
    ],
)
def test_a_different_destination_fails(expected, actual):
    """A login wall must read as a redirect, not as success."""
    assert _check("dom_url_matches", {"url": actual}, expected).verdict is Verdict.FAILED


# ── combining checks ───────────────────────────────────────────────────
def test_every_requirement_must_pass():
    """Three of four satisfied is not "it did what it said it would"."""
    requirements = [
        EvidenceRequirement("process_running"),
        EvidenceRequirement("foreground_window_bundle_id", "com.google.Chrome"),
    ]
    observed = {"pid": 4242, "frontmost_bundle_id": "com.apple.Safari"}
    results = Verifier.check_all(requirements, observed)
    assert Verifier.overall(results) is Verdict.FAILED


def test_all_verified_is_verified():
    requirements = [
        EvidenceRequirement("process_running"),
        EvidenceRequirement("foreground_window_bundle_id", "com.google.Chrome"),
    ]
    observed = {"pid": 4242, "is_running": True, "frontmost_bundle_id": "com.google.Chrome"}
    assert Verifier.overall(Verifier.check_all(requirements, observed)) is Verdict.VERIFIED


def test_no_requirements_is_inconclusive_never_success():
    assert Verifier.overall([]) is Verdict.INCONCLUSIVE


def test_failure_outranks_inconclusive():
    requirements = [
        EvidenceRequirement("process_running"),
        EvidenceRequirement("dom_url_matches", "https://x.test"),
    ]
    observed = {"is_running": False}
    assert Verifier.overall(Verifier.check_all(requirements, observed)) is Verdict.FAILED


# ── browser navigation safety ──────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://metadata/computeMetadata/v1/",
    ],
)
def test_the_browser_refuses_to_reach_inside_the_host(url):
    """A page must not be able to steer the worker at the VPS's own services."""
    with pytest.raises(UnsafeNavigation):
        assert_safe_url(url)


@pytest.mark.parametrize(
    "url", ["https://example.test/a", "http://example.test/b", "https://sub.example.test/c?d=1"]
)
def test_ordinary_web_urls_are_allowed(url):
    assert assert_safe_url(url) == url
