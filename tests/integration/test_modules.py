"""Phase 3.5: morning brief, evening review, focus sessions.

Views over the shared goal engine (blueprint §32), not new engines. The exit test is that
each is a *view*: nothing here computes a forecast of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jarvis.db.models.domain import Task
from jarvis.services.identity import IdentityService
from jarvis.services.modules import ModuleService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
MORNING = datetime(2026, 8, 30, 3, 30, tzinfo=UTC)   # 09:00 IST
EVENING = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)   # 20:30 IST


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("modules@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def modules(session):
    return ModuleService(session)


# ── morning ────────────────────────────────────────────────────────────
async def test_a_quiet_morning_says_so(session, user, modules):
    brief = await modules.morning_brief(user.id, now=MORNING)
    assert brief.headline == "Nothing due today."
    assert brief.at_risk == []
    assert brief.suggested_first is None


async def test_the_greeting_follows_the_users_timezone(session, user, modules):
    assert (await modules.morning_brief(user.id, now=MORNING)).greeting == "Good morning"
    assert (await modules.morning_brief(user.id, now=EVENING)).greeting == "Good evening"


async def test_the_brief_leads_with_the_goal_most_likely_to_fail(session, user, modules):
    goals = modules.goals
    safe = await goals.create_goal(
        user.id, title="Comfortable goal", deadline=MORNING + timedelta(days=5)
    )
    await goals.create_task(user.id, title="Small", goal_id=safe.id, estimate_minutes=30)

    doomed = await goals.create_goal(
        user.id, title="Hackathon submission", deadline=MORNING + timedelta(hours=3)
    )
    await goals.create_task(user.id, title="Huge", goal_id=doomed.id, estimate_minutes=600)
    await session.commit()

    brief = await modules.morning_brief(user.id, now=MORNING)
    assert brief.at_risk, "a goal 600 minutes deep with 3 hours left is at risk"
    assert brief.at_risk[0].title == "Hackathon submission"
    assert "Hackathon submission" in brief.headline
    assert "usable minutes" in brief.at_risk[0].explanation


async def test_the_first_hour_goes_to_the_critical_path_not_the_nearest_deadline(
    session, user, modules
):
    """A ten-minute task due in an hour is not what will sink the day."""
    goals = modules.goals
    goal = await goals.create_goal(
        user.id, title="Submission", deadline=MORNING + timedelta(hours=4)
    )
    design = await goals.create_task(
        user.id, title="Design the thing", goal_id=goal.id, estimate_minutes=200
    )
    await goals.create_task(
        user.id, title="Build on it", goal_id=goal.id, estimate_minutes=200,
        depends_on=[design.id],
    )
    await goals.create_task(
        user.id, title="Trivial errand", due_at=MORNING + timedelta(hours=1),
        estimate_minutes=10,
    )
    await session.commit()

    brief = await modules.morning_brief(user.id, now=MORNING)
    assert brief.suggested_first is not None
    assert brief.suggested_first.title == "Design the thing"


async def test_a_blocked_task_is_never_suggested_first(session, user, modules):
    goals = modules.goals
    goal = await goals.create_goal(
        user.id, title="Chain", deadline=MORNING + timedelta(hours=3)
    )
    first = await goals.create_task(
        user.id, title="Must happen first", goal_id=goal.id, estimate_minutes=300
    )
    await goals.create_task(
        user.id, title="Blocked until then", goal_id=goal.id, estimate_minutes=300,
        depends_on=[first.id],
    )
    await session.commit()

    brief = await modules.morning_brief(user.id, now=MORNING)
    assert brief.suggested_first.title == "Must happen first"


async def test_tasks_due_today_are_listed_in_the_users_day(session, user, modules):
    goals = modules.goals
    await goals.create_task(
        user.id, title="Due today", due_at=MORNING + timedelta(hours=6)
    )
    await goals.create_task(
        user.id, title="Due next week", due_at=MORNING + timedelta(days=7)
    )
    await session.commit()

    brief = await modules.morning_brief(user.id, now=MORNING)
    assert [t.title for t in brief.due_today] == ["Due today"]


# ── evening ────────────────────────────────────────────────────────────
async def test_an_untracked_day_says_so(session, user, modules):
    review = await modules.evening_review(user.id, now=EVENING)
    assert review.headline == "No tracked work today."
    assert review.minutes_worked == 0


async def test_the_review_counts_what_actually_moved(session, user, modules):
    goals = modules.goals
    task = await goals.create_task(user.id, title="Write the report", estimate_minutes=120)
    await goals.record_work(user.id, task.id, active_minutes=95, started_at=MORNING)
    await goals.update_task(user.id, task.id, status="done")
    await session.commit()

    review = await modules.evening_review(user.id, now=EVENING)
    assert review.minutes_worked == 95
    assert review.tasks_completed == 1
    assert review.moved == ["Write the report"]
    assert "1h 35m tracked" in review.headline


async def test_the_review_reports_estimate_accuracy_once_it_knows_you(session, user, modules):
    goals = modules.goals
    for i in range(3):
        task = await goals.create_task(user.id, title=f"T{i}", estimate_minutes=60)
        await goals.record_work(
            user.id, task.id, active_minutes=90, started_at=MORNING, reduce_remaining=False
        )
        await goals.update_task(user.id, task.id, status="done")
    await session.commit()

    review = await modules.evening_review(user.id, now=EVENING)
    assert review.estimate_accuracy == pytest.approx(1.5)


async def test_accuracy_is_withheld_until_there_is_evidence(session, user, modules):
    review = await modules.evening_review(user.id, now=EVENING)
    assert review.estimate_accuracy is None


# ── focus ──────────────────────────────────────────────────────────────
async def test_starting_focus_moves_the_task_into_progress(session, user, modules):
    task = await modules.goals.create_task(
        user.id, title="Deep work", estimate_minutes=90
    )
    await session.commit()

    focus = await modules.start_focus(user.id, task.id, minutes=25)
    await session.commit()

    assert focus.title == "Deep work"
    assert focus.ends_at - focus.started_at == timedelta(minutes=25)
    assert (await session.get(Task, task.id)).status == "in_progress"


async def test_ending_focus_draws_down_the_remaining_estimate(session, user, modules):
    task = await modules.goals.create_task(user.id, title="Deep work", estimate_minutes=90)
    await session.commit()

    await modules.start_focus(user.id, task.id)
    await modules.end_focus(user.id, task.id, active_minutes=25)
    await session.commit()

    assert (await session.get(Task, task.id)).remaining_minutes == 65


async def test_focus_feeds_the_forecast(session, user, modules):
    """The loop that makes it a system: today's session moves tomorrow's prediction."""
    goals = modules.goals
    goal = await goals.create_goal(
        user.id, title="Ship", deadline=datetime.now(UTC) + timedelta(hours=5)
    )
    task = await goals.create_task(
        user.id, title="Core", goal_id=goal.id, estimate_minutes=250
    )
    await session.commit()

    before = await goals.predict_goal(user.id, goal.id, persist=False)
    await modules.start_focus(user.id, task.id)
    await modules.end_focus(user.id, task.id, active_minutes=180)
    await session.commit()
    after = await goals.predict_goal(user.id, goal.id, persist=False)

    assert after.probability > before.probability


async def test_modules_are_scoped_to_their_owner(session, user, modules):
    other = await IdentityService(session).register("intruder@example.com", PASSWORD)
    await modules.goals.create_task(
        user.id, title="Private", due_at=MORNING + timedelta(hours=2)
    )
    await session.commit()

    brief = await modules.morning_brief(other.id, now=MORNING)
    assert brief.due_today == []
