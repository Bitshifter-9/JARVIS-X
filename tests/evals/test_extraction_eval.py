"""Phase 2.3 gate: deadline extraction accuracy.

Exit test from PLAN.md §12: ≥90% on a curated fixture set.

Two modes:

* **Offline (default, runs in CI).** Scores the *resolver and scoring logic* against the
  fixtures using their expected extractions. This proves the harness itself is honest —
  that it would catch a regression — without needing an API key.
* **Live (``--live-eval``).** Runs the real model through the real router and reports the
  measured accuracy. This is the number that goes on the metrics dashboard.

A harness nobody can run offline gets skipped and then rots, which is how a 90% claim
survives long after it stopped being true.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from jarvis.services.extraction import ExtractedDeadline, resolve
from jarvis.services.extraction.resolver import ResolutionError

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "deadline_extraction.json").read_text()
)
CASES = FIXTURES["cases"]
TARGET_ACCURACY = 0.90
TOLERANCE_MINUTES = 60


def _expected_extraction(case: dict) -> ExtractedDeadline:
    expect = case["expect"]
    return ExtractedDeadline(
        has_deadline=expect["has_deadline"],
        title=case["subject"],
        due_at_local=expect["due_at_local"],
        timezone=case["timezone"],
        kind=expect["kind"],
        confidence=0.6 if expect["ambiguous"] else 0.95,
        ambiguity="two plausible readings" if expect["ambiguous"] else None,
        evidence_span=case["body"][:120],
    )


def score(case: dict, extracted: ExtractedDeadline | None) -> tuple[bool, str]:
    """Score one case. Returns ``(passed, reason)``.

    Near-misses matter: "5 Sept 23:59" and "2026-09-05T23:59" are the same answer written
    differently, so comparison happens on resolved instants, with a tolerance.
    """
    expect = case["expect"]
    if extracted is None:
        return (not expect["has_deadline"], "no extraction produced")

    if extracted.has_deadline != expect["has_deadline"]:
        return False, f"has_deadline {extracted.has_deadline} != {expect['has_deadline']}"

    if not expect["has_deadline"]:
        return True, "correctly found no deadline"

    received_at = datetime.fromisoformat(case["received_at"])
    try:
        resolved = resolve(
            extracted, received_at=received_at, default_timezone=case["timezone"]
        )
    except ResolutionError as exc:
        return False, f"unresolvable: {exc}"

    if resolved is None:
        return False, "resolved to nothing"

    expected = resolve(
        _expected_extraction(case), received_at=received_at, default_timezone=case["timezone"]
    )
    drift = abs((resolved.due_at - expected.due_at).total_seconds()) / 60
    if drift > TOLERANCE_MINUTES:
        return False, f"off by {drift:.0f} minutes"

    if expect["ambiguous"] and not resolved.needs_confirmation:
        return False, "ambiguous case was not flagged for confirmation"

    return True, f"within {drift:.0f} minutes"


# ── the harness proves itself ──────────────────────────────────────────
def test_the_fixture_set_is_large_and_varied_enough():
    assert len(CASES) >= 30
    kinds = {c["id"].split("-")[0] for c in CASES}
    assert kinds == {"abs", "rel", "neg", "amb", "adv", "fmt"}
    assert sum(1 for c in CASES if not c["expect"]["has_deadline"]) >= 6, "negatives matter"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_expected_answers_are_self_consistent(case):
    """Every fixture's own expected answer must score as correct.

    If it does not, the fixture is wrong or the resolver is — either way the 90% number
    would be measured against a broken ruler.
    """
    passed, reason = score(case, _expected_extraction(case))
    assert passed, f"{case['id']}: {reason}"


def test_the_scorer_rejects_a_wrong_date():
    """A scorer that passes everything measures nothing."""
    case = next(c for c in CASES if c["id"] == "abs-1")
    wrong = _expected_extraction(case).model_copy(update={"due_at_local": "2026-09-25T23:59"})
    passed, reason = score(case, wrong)
    assert not passed
    assert "off by" in reason


def test_the_scorer_rejects_a_hallucinated_deadline():
    case = next(c for c in CASES if c["id"] == "neg-1")
    invented = _expected_extraction(case).model_copy(
        update={"has_deadline": True, "due_at_local": "2026-09-05T23:59"}
    )
    assert not score(case, invented)[0]


def test_the_scorer_requires_ambiguity_to_be_admitted():
    """A confident guess on an ambiguous message is a failure, not a near-miss."""
    case = next(c for c in CASES if c["id"] == "amb-1")
    overconfident = _expected_extraction(case).model_copy(
        update={"confidence": 0.99, "ambiguity": None}
    )
    passed, reason = score(case, overconfident)
    assert not passed
    assert "not flagged" in reason


def test_a_minute_level_near_miss_still_passes():
    case = next(c for c in CASES if c["id"] == "abs-1")
    close = _expected_extraction(case).model_copy(update={"due_at_local": "2026-09-05T23:30"})
    assert score(case, close)[0]


# ── the live number ────────────────────────────────────────────────────
@pytest.mark.live
async def test_live_extraction_meets_the_target(session, live_eval):
    """The measured accuracy. Run with: pytest tests/evals --live-eval"""
    import uuid

    from jarvis.llm import LLMRouter
    from jarvis.services.extraction import ExtractionService

    service = ExtractionService(session, LLMRouter(session))
    user_id = uuid.uuid4()
    results = []

    for case in CASES:
        outcome = await service.extract(
            user_id=user_id,
            body=case["body"],
            subject=case["subject"],
            sender=case["sender"],
            received_at=datetime.fromisoformat(case["received_at"]),
            timezone=case["timezone"],
        )
        passed, reason = score(case, outcome.raw)
        results.append((case["id"], passed, reason))

    accuracy = sum(1 for _, p, _ in results if p) / len(results)
    failures = [f"  {i}: {r}" for i, p, r in results if not p]
    print(f"\nextraction accuracy: {accuracy:.1%} ({len(results)} cases)")
    if failures:
        print("failures:\n" + "\n".join(failures))

    assert accuracy >= TARGET_ACCURACY, f"{accuracy:.1%} is below the {TARGET_ACCURACY:.0%} target"
