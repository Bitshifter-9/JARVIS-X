"""Phase 1.3 gate: the failure-prediction engine.

Exit test from PLAN.md §12: *severity changes when an estimate or progress changes.*

These are the numbers a judge is asked to believe, so they are tested as arithmetic —
pure functions, fixed clock, no database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.services.goal.graph import DependencyCycle, TaskNode, analyse, would_create_cycle
from jarvis.services.goal.prediction import (
    P80_FACTOR,
    calibration_multiplier,
    effective_remaining,
    predict,
    probability_of_finishing,
    severity_for,
)

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def _node(title, minutes, *, deps=(), optional=False, status="open", priority=2):
    return TaskNode(
        id=uuid.uuid4(),
        title=title,
        remaining_minutes=minutes,
        status=status,
        is_optional=optional,
        priority=priority,
        depends_on=tuple(deps),
    )


def _graph(*nodes):
    return {n.id: n for n in nodes}


# ── the DAG ────────────────────────────────────────────────────────────
def test_critical_path_is_the_longest_chain_not_the_biggest_task():
    a = _node("design", 60)
    b = _node("build", 120, deps=[a.id])
    c = _node("independent spike", 200)          # bigger, but on its own
    nodes = _graph(a, b, c)

    result = analyse(nodes)
    assert result.critical_path_minutes == 200
    assert result.critical_path == [c.id]
    # Total work still counts everything, because one person does it all.
    assert result.total_remaining_minutes == 380


def test_completed_tasks_contribute_no_remaining_work():
    done = _node("finished", 300, status="done")
    todo = _node("todo", 45)
    result = analyse(_graph(done, todo))
    assert result.total_remaining_minutes == 45
    assert result.critical_path_minutes == 45


def test_chain_accumulates_along_dependencies():
    a = _node("a", 30)
    b = _node("b", 30, deps=[a.id])
    c = _node("c", 30, deps=[b.id])
    result = analyse(_graph(a, b, c))
    assert result.critical_path_minutes == 90
    assert result.critical_path == [a.id, b.id, c.id]


def test_blocked_tasks_are_reported_and_available_ones_identified():
    a = _node("a", 30)
    b = _node("b", 30, deps=[a.id])
    result = analyse(_graph(a, b))
    assert result.blocked[b.id] == [a.id]
    assert result.available_now == [a.id]


def test_a_satisfied_dependency_unblocks_its_dependant():
    a = _node("a", 30, status="done")
    b = _node("b", 30, deps=[a.id])
    result = analyse(_graph(a, b))
    assert result.blocked[b.id] == []


def test_cycle_is_detected_and_names_its_members():
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    a = TaskNode(id=a_id, title="a", remaining_minutes=10, depends_on=(b_id,))
    b = TaskNode(id=b_id, title="b", remaining_minutes=10, depends_on=(a_id,))

    with pytest.raises(DependencyCycle) as exc:
        analyse({a_id: a, b_id: b})
    assert set(exc.value.members) == {a_id, b_id}


def test_would_create_cycle_rejects_the_edge_before_it_is_written():
    a = _node("a", 10)
    b = _node("b", 10, deps=[a.id])
    nodes = _graph(a, b)

    assert would_create_cycle(nodes, a.id, b.id) is True   # b already depends on a
    assert would_create_cycle(nodes, a.id, a.id) is True   # self-dependency
    c = _node("c", 10)
    assert would_create_cycle(_graph(a, b, c), c.id, b.id) is False


def test_dependencies_outside_the_graph_are_ignored_not_fatal():
    """A task may depend on something in another goal. That is not this graph's problem."""
    orphan_dep = uuid.uuid4()
    a = _node("a", 30, deps=[orphan_dep])
    result = analyse(_graph(a))
    assert result.total_remaining_minutes == 30


# ── the probability model ──────────────────────────────────────────────
def test_probability_rises_monotonically_with_available_time():
    p50, p80 = 100.0, 140.0
    samples = [probability_of_finishing(t, p50, p80) for t in (30, 60, 100, 140, 300)]
    assert samples == sorted(samples)
    assert 0.0 <= samples[0] < samples[-1] <= 1.0


def test_probability_is_one_half_at_the_median_estimate():
    """A lognormal's median is exp(mu), so exactly half the mass sits below p50."""
    assert probability_of_finishing(100.0, 100.0, 140.0) == pytest.approx(0.5, abs=1e-9)


def test_probability_is_eighty_percent_at_the_p80_estimate():
    """The fit must reproduce the point it was fitted to, or the model is lying."""
    assert probability_of_finishing(140.0, 100.0, 140.0) == pytest.approx(0.80, abs=1e-6)


def test_no_time_left_is_zero_and_no_work_left_is_one():
    assert probability_of_finishing(0.0, 100.0, 140.0) == 0.0
    assert probability_of_finishing(-50.0, 100.0, 140.0) == 0.0
    assert probability_of_finishing(10.0, 0.0, 0.0) == 1.0


def test_severity_thresholds_match_the_blueprint():
    assert severity_for(0.5) == "critical"
    assert severity_for(0.64) == "critical"
    assert severity_for(0.65) == "at_risk"
    assert severity_for(0.99) == "at_risk"
    assert severity_for(1.0) == "on_track"


# ── calibration ────────────────────────────────────────────────────────
def test_calibration_needs_evidence_before_it_claims_to_know_you():
    assert calibration_multiplier([]) == 1.0
    assert calibration_multiplier([(60, 120), (30, 60)]) == 1.0, "two points is noise"


def test_calibration_uses_the_median_so_one_disaster_does_not_dominate():
    pairs = [(60, 90), (60, 90), (60, 90), (60, 3600)]
    assert calibration_multiplier(pairs) == pytest.approx(1.5)


def test_calibration_is_clamped_against_absurd_ratios():
    assert calibration_multiplier([(1, 1000)] * 5) == 4.0
    assert calibration_multiplier([(1000, 1)] * 5) == 0.5


# ── concurrency ────────────────────────────────────────────────────────
def test_solo_worker_is_bound_by_total_work_not_the_critical_path():
    """Twenty independent hours is twenty hours for one person, whatever the DAG says."""
    nodes = _graph(*[_node(f"t{i}", 60) for i in range(20)])
    result = analyse(nodes)
    assert result.critical_path_minutes == 60
    assert effective_remaining(result, concurrency=1) == 1200


def test_extra_people_cannot_shorten_the_critical_path():
    a = _node("a", 100)
    b = _node("b", 100, deps=[a.id])
    result = analyse(_graph(a, b))
    assert effective_remaining(result, concurrency=8) == 200, "the chain is serial"


# ── the gate: severity responds to reality ─────────────────────────────
def _predict(nodes, *, hours_left=4.0, calibration=1.0, buffer=30.0):
    return predict(
        nodes=nodes,
        analysis=analyse(nodes),
        deadline=NOW + timedelta(hours=hours_left),
        now=NOW,
        calibration=calibration,
        safety_buffer_minutes=buffer,
    )


def test_severity_changes_when_an_estimate_changes():
    small = _graph(_node("write report", 60))
    assert _predict(small).severity == "on_track"

    bigger = _graph(_node("write report", 400))
    worse = _predict(bigger)
    assert worse.severity == "critical"
    assert worse.probability < 0.3


def test_severity_changes_when_progress_is_made():
    task = _node("write report", 400)
    before = _predict(_graph(task))
    assert before.severity == "critical"

    progressed = _graph(TaskNode(**{**task.__dict__, "remaining_minutes": 60}))
    after = _predict(progressed)
    assert after.severity == "on_track"
    assert after.probability > before.probability


def test_calibration_makes_the_same_plan_riskier_for_an_optimistic_user():
    nodes = _graph(_node("build", 150))
    honest = _predict(nodes, calibration=1.0)
    optimistic = _predict(nodes, calibration=2.0)
    assert optimistic.probability < honest.probability
    assert optimistic.p50_remaining_minutes == 300


def test_a_goal_with_no_deadline_has_nothing_to_miss():
    result = predict(
        nodes=_graph(_node("someday", 9999)),
        analysis=analyse(_graph(_node("someday", 9999))),
        deadline=None,
        now=NOW,
    )
    assert result.severity == "on_track"
    assert result.probability == 1.0
    assert result.options == []
    assert "no deadline" in result.explanation.lower()


# ── recovery options ───────────────────────────────────────────────────
def test_recovery_offers_dropping_optional_work_and_prices_it():
    core = _node("core submission", 180)
    optional = _node("Alexa animation", 120, optional=True)
    extra = _node("knowledge-graph visualization", 90, optional=True)

    result = _predict(_graph(core, optional, extra), hours_left=4.0)
    assert result.needs_attention

    drop = next(o for o in result.options if o.key == "reduce_scope")
    assert set(drop.tasks_affected) == {"Alexa animation", "knowledge-graph visualization"}
    assert drop.minutes_saved == 210
    assert drop.probability_after > result.probability


def test_options_are_ranked_by_expected_outcome():
    nodes = _graph(_node("core", 300), _node("nice-to-have", 200, optional=True))
    result = _predict(nodes, hours_left=5.0)
    scores = [o.probability_after for o in result.options]
    assert scores == sorted(scores, reverse=True)


def test_help_is_not_offered_when_the_work_is_one_serial_chain():
    """Adding people to a chain of dependencies does not make it shorter."""
    a = _node("a", 200)
    b = _node("b", 200, deps=[a.id])
    result = _predict(_graph(a, b), hours_left=3.0)
    assert "request_help" not in {o.key for o in result.options}


def test_help_is_offered_when_work_is_genuinely_parallelisable():
    nodes = _graph(*[_node(f"independent {i}", 120) for i in range(4)])
    result = _predict(nodes, hours_left=4.0)
    assert "request_help" in {o.key for o in result.options}


def test_an_on_track_goal_is_not_handed_a_recovery_plan():
    result = _predict(_graph(_node("small", 20)), hours_left=8.0)
    assert result.severity == "on_track"
    assert result.options == []


# ── the sentence ───────────────────────────────────────────────────────
def test_explanation_states_both_quantities_and_the_improvement():
    """The judge-visible sentence, generated from real numbers (blueprint §6)."""
    core = _node("core submission", 170)
    optional = _node("Alexa animation", 90, optional=True)
    result = _predict(_graph(core, optional), hours_left=4.0)

    text = result.explanation
    assert f"{result.available_minutes:.0f} usable minutes" in text
    assert f"{result.p80_remaining_minutes:.0f} minutes" in text
    assert f"{result.probability:.0%}" in text
    assert "raises the predicted completion probability" in text
    assert "Alexa animation" in text


def test_explanation_is_honest_when_nothing_helps():
    """No option must be dressed up as a rescue when it changes nothing."""
    result = _predict(_graph(_node("unavoidable", 6000)), hours_left=1.0)
    assert result.severity == "critical"
    assert "needs a decision" in result.explanation or "raises the predicted" in result.explanation


def test_p80_is_the_documented_multiple_of_p50():
    result = _predict(_graph(_node("t", 100)))
    assert result.p80_remaining_minutes == pytest.approx(result.p50_remaining_minutes * P80_FACTOR)


def test_safety_buffer_and_calendar_blocks_reduce_usable_time():
    nodes = _graph(_node("t", 60))
    unbuffered = predict(
        nodes=nodes, analysis=analyse(nodes), deadline=NOW + timedelta(hours=4),
        now=NOW, safety_buffer_minutes=0.0,
    )
    buffered = predict(
        nodes=nodes, analysis=analyse(nodes), deadline=NOW + timedelta(hours=4),
        now=NOW, safety_buffer_minutes=30.0, calendar_blocked_minutes=90.0,
    )
    assert unbuffered.available_minutes == 240
    assert buffered.available_minutes == 120
    assert buffered.probability < unbuffered.probability
