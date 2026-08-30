"""The agent graph.

LangGraph sequences the nine states and persists progress; it decides nothing about
permission. Every effectful path runs through ``policy``, and the executor revalidates
after that regardless — so the security properties survive the framework being swapped
or removed.

Approvals use LangGraph's ``interrupt``: the run stops mid-graph, its state is checkpointed
to Postgres, and the process is free to exit. Resuming days later replays nothing — it
continues from the interrupted node.
"""

from __future__ import annotations

import uuid
from typing import Any

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.services.agent.state import Budget, RunState, Stage
from jarvis.services.policy import Decision
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

log = get_logger(__name__)


class AgentDeps:
    """Everything the graph needs from the outside, passed explicitly.

    Nodes take no globals, so a test can drive the whole graph with fakes.
    """

    def __init__(self, *, planner, gateway, evidence, executor) -> None:  # noqa: ANN001
        self.planner = planner
        self.gateway = gateway
        self.evidence = evidence
        self.executor = executor


def _note(state: RunState, stage: Stage, **detail: Any) -> dict[str, Any]:
    return {"stage": stage.value, "step": state.get("step_index", 0), **detail}


def build_graph(deps: AgentDeps, checkpointer=None):  # noqa: ANN001
    graph = StateGraph(RunState)

    async def ingest(state: RunState) -> dict[str, Any]:
        return {
            "stage": Stage.CLASSIFY.value,
            "timeline": [
                _note(state, Stage.INGEST, observations=len(state.get("observations", [])))
            ],
        }

    async def classify(state: RunState) -> dict[str, Any]:
        intent = await deps.planner.classify(state)
        untrusted = any(
            o.get("trust") == "untrusted" for o in state.get("observations", [])
        )
        return {
            "stage": Stage.CONTEXT.value,
            "intent": intent.get("intent"),
            "urgency": intent.get("urgency"),
            "source_trust": "untrusted" if untrusted else "trusted",
            "timeline": [_note(state, Stage.CLASSIFY, intent=intent.get("intent"))],
        }

    async def context(state: RunState) -> dict[str, Any]:
        gathered = await deps.planner.gather_context(state)
        return {
            "stage": Stage.PLAN.value,
            "context": gathered,
            "timeline": [_note(state, Stage.CONTEXT, keys=sorted(gathered))],
        }

    async def plan(state: RunState) -> dict[str, Any]:
        budget = Budget(**state["budget"])
        if reason := budget.exhausted():
            return {"status": "stopped", "stop_reason": reason, "stage": Stage.COMMIT.value}

        steps = await deps.planner.plan(state)
        if not steps:
            answer = await deps.planner.answer(state)
            return {
                "stage": Stage.COMMIT.value,
                "status": "succeeded",
                "answer": answer,
                "timeline": [_note(state, Stage.PLAN, actions=0)],
            }

        return {
            "stage": Stage.POLICY.value,
            "plan": steps,
            "step_index": 0,
            "current_action": steps[0],
            "timeline": [_note(state, Stage.PLAN, actions=len(steps))],
        }

    async def policy(state: RunState) -> dict[str, Any]:
        action = state["current_action"]
        proposal = await deps.gateway.propose(
            uuid.UUID(state["user_id"]),
            tool=action["tool"],
            args=action.get("args", {}),
            run_id=uuid.UUID(state["run_id"]),
            from_untrusted_source=state.get("source_trust") == "untrusted",
            simulate=state.get("simulate", False),
            rationale=action.get("rationale"),
        )
        return {
            "stage": Stage.EXECUTE.value,
            "action_id": str(proposal.action.id),
            "approval_id": str(proposal.approval.id) if proposal.approval else None,
            "policy_decision": proposal.policy.decision.value,
            "policy_reason": proposal.policy.reason,
            "timeline": [
                _note(
                    state, Stage.POLICY,
                    tool=action["tool"],
                    risk=proposal.policy.risk.value,
                    decision=proposal.policy.decision.value,
                )
            ],
        }

    async def await_approval(state: RunState) -> Command:
        """Suspend until a human decides.

        ``interrupt`` checkpoints and returns control to the caller; the process may exit.
        What comes back on resume is only the decision — the approval record and its
        payload hash live in our table, and the executor re-checks both before dispatch.
        """
        decision = interrupt(
            {
                "kind": "approval_required",
                "approval_id": state["approval_id"],
                "action_id": state["action_id"],
                "tool": state["current_action"]["tool"],
                "reason": state["policy_reason"],
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        if not approved:
            return Command(
                goto="commit",
                update={"status": "stopped", "stop_reason": "approval_rejected"},
            )
        return Command(goto="execute")

    async def execute(state: RunState) -> dict[str, Any]:
        budget = Budget(**state["budget"])
        # Checked here, not only at planning: a repair path re-enters execute without
        # passing through plan, and that is exactly how a loop runs away.
        if reason := budget.exhausted():
            return {
                "stage": Stage.COMMIT.value,
                "status": "stopped",
                "stop_reason": reason,
                "timeline": [_note(state, Stage.EXECUTE, refused=reason)],
            }
        budget.steps_used += 1

        try:
            action = await deps.gateway.authorize_dispatch(uuid.UUID(state["action_id"]))
        except Exception as exc:  # noqa: BLE001
            return {
                "stage": Stage.COMMIT.value,
                "status": "stopped",
                "stop_reason": "dispatch_refused",
                "budget": budget.model_dump(),
                "timeline": [_note(state, Stage.EXECUTE, refused=str(exc)[:200])],
            }

        result = await deps.executor.run(action, simulate=state.get("simulate", False))
        return {
            "stage": Stage.VERIFY.value,
            "result": result,
            "budget": budget.model_dump(),
            "timeline": [_note(state, Stage.EXECUTE, tool=action.tool)],
        }

    async def verify(state: RunState) -> dict[str, Any]:
        outcome = await deps.evidence.verify_action(
            uuid.UUID(state["action_id"]), state.get("result") or {}
        )
        return {
            "stage": Stage.REFLECT.value,
            "verdict": outcome["verdict"],
            "new_evidence": outcome["new_evidence"],
            "timeline": [_note(state, Stage.VERIFY, verdict=outcome["verdict"])],
        }

    async def reflect(state: RunState) -> dict[str, Any]:
        budget = Budget(**state["budget"])
        plan_steps = state.get("plan", [])
        index = state.get("step_index", 0)

        if state.get("verdict") == "verified":
            if index + 1 < len(plan_steps):
                return {
                    "stage": Stage.POLICY.value,
                    "step_index": index + 1,
                    "current_action": plan_steps[index + 1],
                    "timeline": [_note(state, Stage.REFLECT, decision="continue")],
                }
            return {
                "stage": Stage.COMMIT.value,
                "status": "succeeded",
                "timeline": [_note(state, Stage.REFLECT, decision="done")],
            }

        # Repair once on new evidence, then ask. Never "keep trying".
        if not state.get("new_evidence") or budget.replans_used >= budget.max_replans:
            return {
                "stage": Stage.COMMIT.value,
                "status": "awaiting_user",
                "stop_reason": "needs_user_input",
                "timeline": [_note(state, Stage.REFLECT, decision="ask_user")],
            }

        budget.replans_used += 1
        repaired = await deps.planner.repair(state)
        if not repaired:
            return {
                "stage": Stage.COMMIT.value,
                "status": "awaiting_user",
                "stop_reason": "no_repair_available",
                "budget": budget.model_dump(),
                "timeline": [_note(state, Stage.REFLECT, decision="no_repair")],
            }

        return {
            "stage": Stage.POLICY.value,
            "plan": repaired,
            "step_index": 0,
            "current_action": repaired[0],
            "budget": budget.model_dump(),
            "timeline": [
                _note(state, Stage.REFLECT, decision="repair", replans=budget.replans_used)
            ],
        }

    async def commit(state: RunState) -> dict[str, Any]:
        status = state.get("status", "running")
        return {
            "stage": Stage.COMMIT.value,
            "status": "succeeded" if status == "running" else status,
            "timeline": [_note(state, Stage.COMMIT, status=status)],
        }

    graph.add_node("ingest", ingest)
    graph.add_node("classify", classify)
    graph.add_node("context", context)
    graph.add_node("plan", plan)
    graph.add_node("policy", policy)
    graph.add_node("await_approval", await_approval)
    graph.add_node("execute", execute)
    graph.add_node("verify", verify)
    graph.add_node("reflect", reflect)
    graph.add_node("commit", commit)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "classify")
    graph.add_edge("classify", "context")
    graph.add_edge("context", "plan")
    graph.add_conditional_edges(
        "plan", lambda s: "commit" if s.get("stage") == Stage.COMMIT.value else "policy"
    )
    graph.add_conditional_edges("policy", _after_policy)
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "reflect")
    graph.add_conditional_edges(
        "reflect", lambda s: "policy" if s.get("stage") == Stage.POLICY.value else "commit"
    )
    graph.add_edge("commit", END)

    return graph.compile(checkpointer=checkpointer)


def _after_policy(state: RunState) -> str:
    decision = state.get("policy_decision")
    if decision == Decision.DENY.value:
        return "commit"
    if decision == Decision.REQUIRE_APPROVAL.value:
        return "await_approval"
    return "execute"


def default_budget() -> Budget:
    s = get_settings()
    return Budget(
        max_steps=s.max_steps,
        max_replans=s.max_replans,
        max_tokens=s.max_tokens_per_run,
        max_seconds=s.max_run_seconds,
    )
