"""Goals, tasks and predictions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.goal import GoalService

router = APIRouter(prefix="/v1", tags=["goals"])


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    outcome: str | None = None
    deadline: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=2, ge=0, le=4)


class GoalOut(BaseModel):
    id: str
    title: str
    outcome: str | None
    deadline: datetime | None
    timezone: str | None
    priority: int
    status: str
    version: int


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    goal_id: uuid.UUID | None = None
    due_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    estimate_minutes: int | None = Field(default=None, ge=0)
    priority: int = Field(default=2, ge=0, le=4)
    is_optional: bool = False
    depends_on: list[uuid.UUID] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    due_at: datetime | None = None
    estimate_minutes: int | None = Field(default=None, ge=0)
    remaining_minutes: int | None = Field(default=None, ge=0)
    is_optional: bool | None = None


class TaskOut(BaseModel):
    id: str
    goal_id: str | None
    title: str
    status: str
    due_at: datetime | None
    estimate_minutes: int | None
    remaining_minutes: int | None
    priority: int
    is_optional: bool
    evidence_span: str | None
    version: int


class OptionOut(BaseModel):
    key: str
    title: str
    detail: str
    probability_after: float
    minutes_saved: float
    tasks_affected: list[str]
    requires_approval: bool


class PredictionOut(BaseModel):
    goal_id: str
    severity: str
    probability: float
    finish_ratio: float
    available_minutes: float
    p50_remaining_minutes: float
    p80_remaining_minutes: float
    calibration_multiplier: float
    critical_path: list[str]
    explanation: str
    options: list[OptionOut]


def _goal_out(goal: Any) -> GoalOut:
    return GoalOut(
        id=str(goal.id), title=goal.title, outcome=goal.outcome, deadline=goal.deadline,
        timezone=goal.timezone, priority=goal.priority, status=goal.status, version=goal.version,
    )


def _task_out(task: Any) -> TaskOut:
    return TaskOut(
        id=str(task.id), goal_id=str(task.goal_id) if task.goal_id else None,
        title=task.title, status=task.status, due_at=task.due_at,
        estimate_minutes=task.estimate_minutes, remaining_minutes=task.remaining_minutes,
        priority=task.priority, is_optional=task.is_optional,
        evidence_span=task.evidence_span, version=task.version,
    )


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(body: GoalCreate, user: CurrentUser, session: SessionDep) -> GoalOut:
    goal = await GoalService(session).create_goal(
        user.id, title=body.title, outcome=body.outcome, deadline=body.deadline,
        timezone=body.timezone, priority=body.priority,
    )
    return _goal_out(goal)


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(user: CurrentUser, session: SessionDep) -> list[GoalOut]:
    return [_goal_out(g) for g in await GoalService(session).list_goals(user.id)]


@router.get("/goals/{goal_id}", response_model=GoalOut)
async def get_goal(goal_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> GoalOut:
    return _goal_out(await GoalService(session).get_goal(user.id, goal_id))


@router.get("/goals/{goal_id}/prediction", response_model=PredictionOut)
async def get_prediction(
    goal_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> PredictionOut:
    """The differentiating feature: will this plan miss, and what would fix it."""
    prediction = await GoalService(session).predict_goal(user.id, goal_id)
    return PredictionOut(
        goal_id=str(goal_id),
        severity=prediction.severity,
        probability=prediction.probability,
        finish_ratio=(
            prediction.finish_ratio if prediction.finish_ratio != float("inf") else -1.0
        ),
        available_minutes=(
            prediction.available_minutes
            if prediction.available_minutes != float("inf") else -1.0
        ),
        p50_remaining_minutes=prediction.p50_remaining_minutes,
        p80_remaining_minutes=prediction.p80_remaining_minutes,
        calibration_multiplier=prediction.calibration_multiplier,
        critical_path=[str(t) for t in prediction.critical_path],
        explanation=prediction.explanation,
        options=[
            OptionOut(
                key=o.key, title=o.title, detail=o.detail,
                probability_after=o.probability_after, minutes_saved=o.minutes_saved,
                tasks_affected=o.tasks_affected, requires_approval=o.requires_approval,
            )
            for o in prediction.options
        ],
    )


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, user: CurrentUser, session: SessionDep) -> TaskOut:
    task = await GoalService(session).create_task(
        user.id, title=body.title, goal_id=body.goal_id, due_at=body.due_at,
        timezone=body.timezone, estimate_minutes=body.estimate_minutes,
        priority=body.priority, is_optional=body.is_optional, depends_on=body.depends_on,
    )
    return _task_out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    user: CurrentUser,
    session: SessionDep,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> TaskOut:
    """Update a task. ``If-Match`` carries the version, for optimistic concurrency."""
    expected = int(if_match) if if_match and if_match.isdigit() else None
    task = await GoalService(session).update_task(
        user.id, task_id, expected_version=expected,
        **body.model_dump(exclude_unset=True),
    )
    return _task_out(task)


@router.post("/tasks/{task_id}/acknowledge")
async def acknowledge(
    task_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, int]:
    """Acknowledge an alert, cancelling every later one for this task."""
    return {"cancelled_alerts": await GoalService(session).acknowledge_task(user.id, task_id)}


@router.post("/tasks/{task_id}/work")
async def record_work(
    task_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    active_minutes: int,
    interruptions: int = 0,
) -> dict[str, Any]:
    """Log time spent. Today's session is tomorrow's calibration multiplier."""
    goals = GoalService(session)
    await goals.record_work(
        user.id, task_id, active_minutes=active_minutes, interruptions=interruptions
    )
    task = await goals.get_task(user.id, task_id)
    return {"remaining_minutes": task.remaining_minutes, "version": task.version}
