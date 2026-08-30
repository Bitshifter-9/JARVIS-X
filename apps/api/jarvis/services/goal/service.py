"""Goal and task service: the DAG, the schedule ladder, and the forecast.

Two invariants this module is responsible for, both from blueprint §7:

* **A task update bumps ``version``.** Every pending schedule for that task then holds a
  stale version and exits without alerting. That is the entire mechanism behind
  "acknowledging cancels every later alert".
* **Deadlines are stored as a confirmed UTC instant plus an IANA zone**, and reminders are
  *computed* from that pair — never parsed again from text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.core.errors import Conflict, NotFound
from jarvis.core.logging import get_logger
from jarvis.db.models.domain import (
    Goal,
    GoalPrediction,
    Task,
    TaskDependency,
    WorkSession,
)
from jarvis.db.models.ops import Schedule
from jarvis.services.goal.graph import GraphAnalysis, TaskNode, analyse, would_create_cycle
from jarvis.services.goal.prediction import (
    Prediction,
    calibration_multiplier,
    predict,
    schedule_offsets,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


class GoalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── goals ──────────────────────────────────────────────────────────
    async def create_goal(
        self,
        user_id: uuid.UUID,
        *,
        title: str,
        deadline: datetime | None = None,
        timezone: str | None = None,
        outcome: str | None = None,
        priority: int = 2,
        success_metric: dict[str, Any] | None = None,
    ) -> Goal:
        if deadline is not None and deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware; store a confirmed UTC instant")

        goal = Goal(
            user_id=user_id,
            title=title,
            outcome=outcome,
            deadline=deadline.astimezone(UTC) if deadline else None,
            timezone=timezone,
            priority=priority,
            success_metric=success_metric,
        )
        self.session.add(goal)
        await self.session.flush()
        return goal

    async def get_goal(self, user_id: uuid.UUID, goal_id: uuid.UUID) -> Goal:
        goal = await self.session.scalar(
            select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
        )
        if goal is None:
            raise NotFound("Goal")
        return goal

    async def list_goals(self, user_id: uuid.UUID, *, status: str | None = None) -> list[Goal]:
        query = select(Goal).where(Goal.user_id == user_id)
        if status:
            query = query.where(Goal.status == status)
        return list((await self.session.scalars(query.order_by(Goal.deadline.nullslast()))).all())

    # ── tasks ──────────────────────────────────────────────────────────
    async def create_task(
        self,
        user_id: uuid.UUID,
        *,
        title: str,
        goal_id: uuid.UUID | None = None,
        due_at: datetime | None = None,
        timezone: str | None = None,
        estimate_minutes: int | None = None,
        priority: int = 2,
        is_optional: bool = False,
        source_id: uuid.UUID | None = None,
        confidence: float | None = None,
        evidence_span: str | None = None,
        depends_on: list[uuid.UUID] | None = None,
    ) -> Task:
        if due_at is not None and due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware; store a confirmed UTC instant")

        task = Task(
            user_id=user_id,
            goal_id=goal_id,
            title=title,
            due_at=due_at.astimezone(UTC) if due_at else None,
            timezone=timezone,
            estimate_minutes=estimate_minutes,
            # Remaining starts at the estimate: nothing has been done yet.
            remaining_minutes=estimate_minutes,
            priority=priority,
            is_optional=is_optional,
            source_id=source_id,
            confidence=confidence,
            evidence_span=evidence_span,
        )
        self.session.add(task)
        await self.session.flush()

        for dep in depends_on or []:
            await self.add_dependency(user_id, task.id, dep)

        if task.due_at:
            await self.reschedule_task(task)
        return task

    async def get_task(self, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        task = await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        if task is None:
            raise NotFound("Task")
        return task

    async def update_task(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        expected_version: int | None = None,
        **changes: Any,
    ) -> Task:
        """Update a task, bumping its version and re-arming its schedules.

        ``expected_version`` gives optimistic concurrency: two clients editing the same
        task cannot silently overwrite each other.
        """
        task = await self.get_task(user_id, task_id)
        if expected_version is not None and task.version != expected_version:
            raise Conflict(
                f"Task was modified by someone else (version {task.version}, "
                f"expected {expected_version})"
            )

        due_changed = False
        for field, value in changes.items():
            if value is None and field not in ("due_at", "goal_id"):
                continue
            if field == "due_at":
                if value is not None and value.tzinfo is None:
                    raise ValueError("due_at must be timezone-aware")
                value = value.astimezone(UTC) if value else None
                due_changed = value != task.due_at
            if field == "status" and value in ("done", "cancelled"):
                task.completed_at = datetime.now(UTC)
                task.remaining_minutes = 0
            setattr(task, field, value)

        # The version bump is what invalidates every pending schedule for this task.
        task.version += 1
        await self.session.flush()

        if due_changed or "status" in changes:
            await self.reschedule_task(task)

        log.info("task_updated", task_id=str(task.id), version=task.version)
        return task

    async def add_dependency(
        self, user_id: uuid.UUID, task_id: uuid.UUID, depends_on: uuid.UUID
    ) -> None:
        """Add an edge, refusing one that would create a cycle.

        Rejecting here is far kinder than discovering the cycle later, when the prediction
        engine cannot produce a number and the user cannot see why.
        """
        task = await self.get_task(user_id, task_id)
        await self.get_task(user_id, depends_on)  # tenant check on the other end too

        nodes = await self._nodes_for_goal(user_id, task.goal_id)
        nodes.setdefault(task_id, TaskNode(id=task_id, title=task.title, remaining_minutes=0))
        if would_create_cycle(nodes, task_id, depends_on):
            raise Conflict("That dependency would create a cycle")

        self.session.add(TaskDependency(task_id=task_id, depends_on=depends_on))
        await self.session.flush()

    async def remove_dependency(self, task_id: uuid.UUID, depends_on: uuid.UUID) -> None:
        await self.session.execute(
            delete(TaskDependency).where(
                TaskDependency.task_id == task_id, TaskDependency.depends_on == depends_on
            )
        )

    # ── the schedule ladder ────────────────────────────────────────────
    async def reschedule_task(self, task: Task) -> list[Schedule]:
        """Re-arm the T-24h/2h/1h/15m ladder for a task's current version.

        Old rows are cancelled rather than deleted: the audit timeline should be able to
        show that an alert *was* planned and why it stopped being.
        """
        await self.session.execute(
            Schedule.__table__.update()
            .where(
                Schedule.task_id == task.id,
                Schedule.status == "pending",
                Schedule.task_version != task.version,
            )
            .values(status="cancelled", cancelled_reason="task updated")
        )

        if task.due_at is None or task.status in ("done", "cancelled"):
            await self.session.execute(
                Schedule.__table__.update()
                .where(Schedule.task_id == task.id, Schedule.status == "pending")
                .values(status="cancelled", cancelled_reason=f"task {task.status}")
            )
            await self.session.flush()
            return []

        now = datetime.now(UTC)
        created: list[Schedule] = []
        for kind, offset in schedule_offsets():
            fire_at = task.due_at - offset
            if fire_at <= now:
                continue  # already past; a reminder for a moment that has gone is noise
            schedule = Schedule(
                user_id=task.user_id,
                task_id=task.id,
                task_version=task.version,
                kind=kind,
                fire_at=fire_at,
                payload={"task_id": str(task.id), "task_version": task.version, "kind": kind},
            )
            self.session.add(schedule)
            created.append(schedule)

        await self.session.flush()
        log.info(
            "task_schedules_armed",
            task_id=str(task.id), version=task.version, count=len(created),
        )
        return created

    async def acknowledge_task(self, user_id: uuid.UUID, task_id: uuid.UUID) -> int:
        """Acknowledge an alert, cancelling every later one for this task.

        Implemented by bumping the version, which is the same mechanism an edit uses —
        one rule, not two.
        """
        task = await self.get_task(user_id, task_id)
        task.version += 1
        await self.session.flush()

        result = await self.session.execute(
            Schedule.__table__.update()
            .where(Schedule.task_id == task.id, Schedule.status == "pending")
            .values(status="cancelled", cancelled_reason="acknowledged")
            .returning(Schedule.id)
        )
        cancelled = len(result.fetchall())
        await self.session.flush()
        log.info("task_acknowledged", task_id=str(task.id), cancelled_alerts=cancelled)
        return cancelled

    # ── analysis ───────────────────────────────────────────────────────
    async def _nodes_for_goal(
        self, user_id: uuid.UUID, goal_id: uuid.UUID | None
    ) -> dict[uuid.UUID, TaskNode]:
        query = select(Task).where(Task.user_id == user_id)
        query = query.where(Task.goal_id == goal_id) if goal_id else query.where(
            Task.goal_id.is_(None)
        )
        tasks = list((await self.session.scalars(query)).all())
        if not tasks:
            return {}

        task_ids = [t.id for t in tasks]
        edges = (
            await self.session.execute(
                select(TaskDependency.task_id, TaskDependency.depends_on).where(
                    TaskDependency.task_id.in_(task_ids)
                )
            )
        ).all()
        deps: dict[uuid.UUID, list[uuid.UUID]] = {}
        for task_id, depends_on in edges:
            deps.setdefault(task_id, []).append(depends_on)

        return {
            t.id: TaskNode(
                id=t.id,
                title=t.title,
                remaining_minutes=float(
                    t.remaining_minutes if t.remaining_minutes is not None
                    else (t.estimate_minutes or 0)
                ),
                status=t.status,
                is_optional=t.is_optional,
                priority=t.priority,
                depends_on=tuple(deps.get(t.id, ())),
            )
            for t in tasks
        }

    async def analyse_goal(self, user_id: uuid.UUID, goal_id: uuid.UUID) -> GraphAnalysis:
        return analyse(await self._nodes_for_goal(user_id, goal_id))

    async def user_calibration(self, user_id: uuid.UUID) -> float:
        """How wrong this user's estimates usually run, from completed work."""
        rows = (
            await self.session.execute(
                select(Task.estimate_minutes, func.sum(WorkSession.active_minutes))
                .join(WorkSession, WorkSession.task_id == Task.id)
                .where(
                    Task.user_id == user_id,
                    Task.status == "done",
                    Task.estimate_minutes.isnot(None),
                    Task.estimate_minutes > 0,
                )
                .group_by(Task.id, Task.estimate_minutes)
            )
        ).all()
        return calibration_multiplier([(float(e), float(a or 0)) for e, a in rows])

    async def predict_goal(
        self,
        user_id: uuid.UUID,
        goal_id: uuid.UUID,
        *,
        now: datetime | None = None,
        calendar_blocked_minutes: float = 0.0,
        persist: bool = True,
    ) -> Prediction:
        goal = await self.get_goal(user_id, goal_id)
        nodes = await self._nodes_for_goal(user_id, goal_id)
        graph = analyse(nodes)

        prediction = predict(
            nodes=nodes,
            analysis=graph,
            deadline=goal.deadline,
            now=now or datetime.now(UTC),
            calendar_blocked_minutes=calendar_blocked_minutes,
            calibration=await self.user_calibration(user_id),
        )

        if persist and goal.deadline is not None:
            self.session.add(
                GoalPrediction(
                    goal_id=goal.id,
                    user_id=user_id,
                    available_minutes=prediction.available_minutes,
                    p50_remaining_minutes=prediction.p50_remaining_minutes,
                    p80_remaining_minutes=prediction.p80_remaining_minutes,
                    finish_ratio=(
                        prediction.finish_ratio if prediction.finish_ratio != float("inf") else -1.0
                    ),
                    probability=prediction.probability,
                    severity=prediction.severity,
                    calibration_multiplier=prediction.calibration_multiplier,
                    critical_path=[str(t) for t in prediction.critical_path],
                    options=[
                        {
                            "key": o.key,
                            "title": o.title,
                            "detail": o.detail,
                            "probability_after": o.probability_after,
                            "minutes_saved": o.minutes_saved,
                            "tasks_affected": o.tasks_affected,
                            "requires_approval": o.requires_approval,
                        }
                        for o in prediction.options
                    ],
                    explanation=prediction.explanation,
                )
            )
            await self.session.flush()

        return prediction

    # ── work sessions ──────────────────────────────────────────────────
    async def record_work(
        self,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        active_minutes: int,
        started_at: datetime | None = None,
        interruptions: int = 0,
        reduce_remaining: bool = True,
    ) -> WorkSession:
        """Log time spent, and draw down the remaining estimate.

        This is the loop that makes calibration real: today's session is tomorrow's
        multiplier.
        """
        task = await self.get_task(user_id, task_id)
        started = started_at or (datetime.now(UTC) - timedelta(minutes=active_minutes))

        session_row = WorkSession(
            user_id=user_id,
            task_id=task_id,
            started_at=started,
            ended_at=started + timedelta(minutes=active_minutes),
            active_minutes=active_minutes,
            interruptions=interruptions,
        )
        self.session.add(session_row)

        if reduce_remaining and task.remaining_minutes is not None:
            task.remaining_minutes = max(0, task.remaining_minutes - active_minutes)
            task.version += 1

        await self.session.flush()
        return session_row
