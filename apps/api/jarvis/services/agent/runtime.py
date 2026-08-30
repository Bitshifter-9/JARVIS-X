"""Running the agent graph against real services."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from jarvis.core.config import get_settings
from jarvis.core.correlation import ensure_correlation_id
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, AgentRun, RunStatus
from jarvis.services.agent.graph import AgentDeps, build_graph, default_budget
from jarvis.services.agent.state import Observation, new_run_state
from jarvis.services.evidence import EvidenceService
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


@dataclass
class RunHandle:
    run_id: uuid.UUID
    state: dict[str, Any]
    interrupt: dict[str, Any] | None

    @property
    def awaiting_approval(self) -> bool:
        return self.interrupt is not None

    @property
    def status(self) -> str:
        if self.awaiting_approval:
            return RunStatus.AWAITING_APPROVAL.value
        return self.state.get("status", "running")


class EvidenceAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def verify_action(self, action_id: uuid.UUID, observed: dict[str, Any]) -> dict[str, Any]:
        action = await self.session.get(Action, action_id, populate_existing=True)
        outcome = await EvidenceService(self.session).verify(action, observed)
        return {
            "verdict": outcome.verdict.value,
            "new_evidence": outcome.new_evidence,
            "summary": outcome.summary,
        }


class AgentRuntime:
    """Starts and resumes runs.

    ``thread_id`` is the run id, so LangGraph's checkpoint and our ``agent_runs`` row are
    the same conversation seen from two sides.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        planner,  # noqa: ANN001
        executor,  # noqa: ANN001
        checkpointer=None,  # noqa: ANN001
    ) -> None:
        self.session = session
        self.deps = AgentDeps(
            planner=planner,
            gateway=ToolGateway(session),
            evidence=EvidenceAdapter(session),
            executor=executor,
        )
        self.checkpointer = checkpointer
        self.graph = build_graph(self.deps, checkpointer=checkpointer)

    async def start(
        self,
        user_id: uuid.UUID,
        *,
        observations: list[Observation],
        goal_id: uuid.UUID | None = None,
        trigger: str = "chat",
        simulate: bool = False,
    ) -> RunHandle:
        run_id = uuid7()
        correlation_id = ensure_correlation_id()

        self.session.add(
            AgentRun(
                id=run_id,
                user_id=user_id,
                goal_id=goal_id,
                trigger=trigger,
                correlation_id=correlation_id,
                budget=default_budget().model_dump(),
                simulate=simulate,
            )
        )
        await self.session.flush()

        state = new_run_state(
            run_id=run_id,
            user_id=user_id,
            correlation_id=correlation_id,
            trigger=trigger,
            goal_id=goal_id,
            simulate=simulate,
            observations=observations,
            budget=default_budget(),
        )
        return await self._drive(run_id, state)

    async def resume(self, run_id: uuid.UUID, *, approved: bool) -> RunHandle:
        from langgraph.types import Command

        return await self._drive(run_id, Command(resume={"approved": approved}))

    async def _drive(self, run_id: uuid.UUID, payload: Any) -> RunHandle:
        config = {"configurable": {"thread_id": str(run_id)}}
        state = await self.graph.ainvoke(payload, config=config)

        pending = None
        if self.checkpointer is not None:
            snapshot = await self.graph.aget_state(config)
            interrupts = getattr(snapshot, "interrupts", ()) or ()
            if interrupts:
                pending = interrupts[0].value

        await self._persist(run_id, state, pending)
        return RunHandle(run_id=run_id, state=dict(state), interrupt=pending)

    async def _persist(
        self, run_id: uuid.UUID, state: dict[str, Any], pending: dict[str, Any] | None
    ) -> None:
        run = await self.session.get(AgentRun, run_id)
        if run is None:
            return
        run.state = state.get("stage", run.state)
        run.step_index = state.get("step_index", 0)
        run.budget = state.get("budget", run.budget)
        run.stop_reason = state.get("stop_reason")
        run.status = (
            RunStatus.AWAITING_APPROVAL.value if pending else state.get("status", "running")
        )
        await self.session.flush()


@asynccontextmanager
async def postgres_checkpointer():
    """LangGraph's Postgres checkpointer, on the same database as everything else.

    Co-located deliberately: a checkpoint that lives elsewhere can disagree with the
    ``agent_runs`` row it describes, and reconciling two stores is worse than one hop.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        yield saver
