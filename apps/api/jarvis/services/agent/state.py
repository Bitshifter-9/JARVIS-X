"""Agent run state.

The nine states of blueprint §4, as a typed graph state. LangGraph executes it and
persists it; what each state is *allowed* to do stays defined here and in the policy
engine.
"""

from __future__ import annotations

import enum
import uuid
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


class Stage(enum.StrEnum):
    INGEST = "ingest"
    CLASSIFY = "classify"
    CONTEXT = "context"
    PLAN = "plan"
    POLICY = "policy"
    EXECUTE = "execute"
    VERIFY = "verify"
    REFLECT = "reflect"
    COMMIT = "commit"


class Budget(BaseModel):
    """Ceilings the harness enforces. The model never sees or sets these."""

    max_steps: int = 8
    max_replans: int = 2
    max_tokens: int = 20_000
    max_seconds: int = 180

    steps_used: int = 0
    replans_used: int = 0
    tokens_used: int = 0

    def exhausted(self) -> str | None:
        if self.steps_used >= self.max_steps:
            return "step_budget_exhausted"
        if self.replans_used > self.max_replans:
            return "replan_budget_exhausted"
        if self.tokens_used >= self.max_tokens:
            return "token_budget_exhausted"
        return None


class Observation(BaseModel):
    source: str
    content: str
    trust: Literal["trusted", "untrusted"] = "untrusted"


class PlannedAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


def _append(left: list, right: list) -> list:
    return (left or []) + (right or [])


class RunState(TypedDict, total=False):
    run_id: str
    user_id: str
    goal_id: str | None
    correlation_id: str
    trigger: str
    simulate: bool

    stage: str
    budget: dict[str, Any]
    observations: Annotated[list[dict[str, Any]], _append]
    timeline: Annotated[list[dict[str, Any]], _append]

    intent: str | None
    urgency: str | None
    source_trust: str
    context: dict[str, Any]

    plan: list[dict[str, Any]]
    step_index: int
    current_action: dict[str, Any] | None
    action_id: str | None
    approval_id: str | None

    policy_decision: str | None
    policy_reason: str | None

    result: dict[str, Any] | None
    verdict: str | None
    new_evidence: bool

    status: str
    stop_reason: str | None
    answer: str | None


def new_run_state(
    *,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    correlation_id: str,
    trigger: str = "chat",
    goal_id: uuid.UUID | None = None,
    simulate: bool = False,
    observations: list[Observation] | None = None,
    budget: Budget | None = None,
) -> RunState:
    return RunState(
        run_id=str(run_id),
        user_id=str(user_id),
        goal_id=str(goal_id) if goal_id else None,
        correlation_id=correlation_id,
        trigger=trigger,
        simulate=simulate,
        stage=Stage.INGEST.value,
        budget=(budget or Budget()).model_dump(),
        observations=[o.model_dump() for o in (observations or [])],
        timeline=[],
        source_trust="untrusted",
        context={},
        plan=[],
        step_index=0,
        current_action=None,
        action_id=None,
        approval_id=None,
        policy_decision=None,
        policy_reason=None,
        result=None,
        verdict=None,
        new_evidence=False,
        status="running",
        stop_reason=None,
        answer=None,
    )
