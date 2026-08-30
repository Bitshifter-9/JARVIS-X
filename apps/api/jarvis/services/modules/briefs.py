"""Morning brief, evening review and focus sessions.

Blueprint §32 lists these as product modules on the shared goal engine — views, not new
engines. Each one answers a different question:

* morning — what will hurt today, and where should the first hour go
* evening — what actually moved, and what the estimates got wrong
* focus — one task, a clock, and nothing else
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.db.models.domain import Goal, Task, WorkSession
from jarvis.db.models.identity import User
from jarvis.services.goal import GoalService
from jarvis.services.goal.prediction import Prediction
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class RiskLine:
    goal_id: uuid.UUID
    title: str
    severity: str
    probability: float
    explanation: str
    top_option: str | None = None


@dataclass
class MorningBrief:
    generated_at: datetime
    greeting: str
    usable_minutes: float
    at_risk: list[RiskLine] = field(default_factory=list)
    due_today: list[Task] = field(default_factory=list)
    suggested_first: Task | None = None

    @property
    def headline(self) -> str:
        if not self.at_risk:
            return (
                f"{len(self.due_today)} task(s) due today, nothing at risk."
                if self.due_today
                else "Nothing due today."
            )
        worst = min(self.at_risk, key=lambda r: r.probability)
        return f"{worst.title} is at {worst.probability:.0%} — {worst.severity.replace('_', ' ')}."


@dataclass
class EveningReview:
    generated_at: datetime
    minutes_worked: int
    tasks_completed: int
    estimate_accuracy: float | None
    moved: list[str] = field(default_factory=list)
    still_open: list[str] = field(default_factory=list)
    tomorrow: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        if self.minutes_worked == 0:
            return "No tracked work today."
        hours, minutes = divmod(self.minutes_worked, 60)
        spent = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        return f"{spent} tracked, {self.tasks_completed} task(s) finished."


@dataclass
class FocusSession:
    task_id: uuid.UUID
    title: str
    started_at: datetime
    planned_minutes: int

    @property
    def ends_at(self) -> datetime:
        return self.started_at + timedelta(minutes=self.planned_minutes)


class ModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.goals = GoalService(session)

    async def _timezone(self, user_id: uuid.UUID) -> ZoneInfo:
        user = await self.session.get(User, user_id)
        return ZoneInfo(user.timezone if user else "UTC")

    async def _local_day(self, user_id: uuid.UUID, now: datetime) -> tuple[datetime, datetime]:
        zone = await self._timezone(user_id)
        local = now.astimezone(zone)
        start = datetime.combine(local.date(), time.min, tzinfo=zone)
        return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)

    async def morning_brief(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> MorningBrief:
        moment = now or datetime.now(UTC)
        day_start, day_end = await self._local_day(user_id, moment)

        due_today = list(
            (
                await self.session.scalars(
                    select(Task)
                    .where(
                        Task.user_id == user_id,
                        Task.status.in_(["open", "in_progress"]),
                        Task.due_at >= day_start,
                        Task.due_at < day_end,
                    )
                    .order_by(Task.due_at)
                )
            ).all()
        )

        at_risk: list[RiskLine] = []
        for goal in await self.goals.list_goals(user_id, status="active"):
            if goal.deadline is None:
                continue
            prediction = await self.goals.predict_goal(
                user_id, goal.id, now=moment, persist=False
            )
            if prediction.needs_attention:
                at_risk.append(_risk_line(goal, prediction))

        at_risk.sort(key=lambda r: r.probability)

        return MorningBrief(
            generated_at=moment,
            greeting=_greeting(moment, await self._timezone(user_id)),
            usable_minutes=max(0.0, (day_end - moment).total_seconds() / 60),
            at_risk=at_risk,
            due_today=due_today,
            suggested_first=await self._suggest_first(user_id, at_risk, due_today),
        )

    async def _suggest_first(
        self, user_id: uuid.UUID, at_risk: list[RiskLine], due_today: list[Task]
    ) -> Task | None:
        """The first hour goes to the critical path of the goal most likely to fail.

        Not to whatever is due soonest: a task due in an hour that takes ten minutes is
        not the thing that will sink the day.
        """
        if at_risk:
            analysis = await self.goals.analyse_goal(user_id, at_risk[0].goal_id)
            startable = set(analysis.available_now) & set(analysis.critical_path)
            if startable:
                return await self.session.get(Task, next(iter(startable)))
        return due_today[0] if due_today else None

    async def evening_review(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> EveningReview:
        moment = now or datetime.now(UTC)
        day_start, day_end = await self._local_day(user_id, moment)

        minutes = await self.session.scalar(
            select(func.coalesce(func.sum(WorkSession.active_minutes), 0)).where(
                WorkSession.user_id == user_id,
                WorkSession.started_at >= day_start,
                WorkSession.started_at < day_end,
            )
        )

        completed = list(
            (
                await self.session.scalars(
                    select(Task).where(
                        Task.user_id == user_id,
                        Task.completed_at >= day_start,
                        Task.completed_at < day_end,
                    )
                )
            ).all()
        )

        open_tasks = list(
            (
                await self.session.scalars(
                    select(Task)
                    .where(
                        Task.user_id == user_id,
                        Task.status.in_(["open", "in_progress"]),
                        Task.due_at.isnot(None),
                    )
                    .order_by(Task.due_at)
                    .limit(5)
                )
            ).all()
        )

        calibration = await self.goals.user_calibration(user_id)
        return EveningReview(
            generated_at=moment,
            minutes_worked=int(minutes or 0),
            tasks_completed=len(completed),
            estimate_accuracy=None if calibration == 1.0 else calibration,
            moved=[t.title for t in completed],
            still_open=[t.title for t in open_tasks],
            tomorrow=[
                t.title for t in open_tasks if t.due_at and t.due_at < day_end + timedelta(days=1)
            ],
        )

    async def start_focus(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, minutes: int = 25
    ) -> FocusSession:
        task = await self.goals.get_task(user_id, task_id)
        started = datetime.now(UTC)

        self.session.add(
            WorkSession(
                user_id=user_id, task_id=task_id, started_at=started,
                active_minutes=0, source="focus",
            )
        )
        if task.status == "open":
            task.status = "in_progress"
            task.version += 1
        await self.session.flush()

        return FocusSession(
            task_id=task_id, title=task.title, started_at=started, planned_minutes=minutes
        )

    async def end_focus(
        self, user_id: uuid.UUID, task_id: uuid.UUID, *, active_minutes: int
    ) -> WorkSession:
        return await self.goals.record_work(
            user_id, task_id, active_minutes=active_minutes
        )


def _greeting(moment: datetime, zone: ZoneInfo) -> str:
    hour = moment.astimezone(zone).hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


def _risk_line(goal: Goal, prediction: Prediction) -> RiskLine:
    best = (
        max(
            (o for o in prediction.options if not o.requires_approval),
            key=lambda o: o.probability_after,
            default=None,
        )
        or (prediction.options[0] if prediction.options else None)
    )
    return RiskLine(
        goal_id=goal.id,
        title=goal.title,
        severity=prediction.severity,
        probability=prediction.probability,
        explanation=prediction.explanation,
        top_option=best.title if best else None,
    )
