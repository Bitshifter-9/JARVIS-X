"""The task DAG: topological ordering, cycle detection and the critical path.

Pure functions over plain dataclasses. No database, no clock, no I/O — so the arithmetic
that decides whether you are going to miss a deadline can be tested exhaustively and read
by a person who does not know SQLAlchemy.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field


class DependencyCycle(ValueError):
    """A dependency cycle. Reported with the members, because "there is a cycle
    somewhere" is not actionable."""

    def __init__(self, members: list[uuid.UUID]) -> None:
        self.members = members
        super().__init__(f"dependency cycle among {len(members)} tasks: {members}")


@dataclass(frozen=True)
class TaskNode:
    id: uuid.UUID
    title: str
    remaining_minutes: float
    status: str = "open"
    is_optional: bool = False
    priority: int = 2
    depends_on: tuple[uuid.UUID, ...] = ()

    @property
    def is_done(self) -> bool:
        return self.status in ("done", "cancelled")

    @property
    def outstanding_minutes(self) -> float:
        """Work still to do. A completed task contributes nothing, whatever its estimate."""
        return 0.0 if self.is_done else max(0.0, self.remaining_minutes)


@dataclass
class GraphAnalysis:
    order: list[uuid.UUID]
    critical_path: list[uuid.UUID]
    critical_path_minutes: float
    total_remaining_minutes: float
    optional_minutes: float
    blocked: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)

    @property
    def available_now(self) -> list[uuid.UUID]:
        """Tasks whose dependencies are all satisfied — what can actually be started."""
        return [tid for tid, blockers in self.blocked.items() if not blockers]


def topological_order(nodes: dict[uuid.UUID, TaskNode]) -> list[uuid.UUID]:
    """Kahn's algorithm. Raises ``DependencyCycle`` naming the tasks involved.

    Dependencies pointing outside the set are ignored rather than raising: a task may
    legitimately depend on something in another goal, and that is not this graph's problem.
    """
    indegree: dict[uuid.UUID, int] = {tid: 0 for tid in nodes}
    dependents: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)

    for tid, node in nodes.items():
        for dep in node.depends_on:
            if dep in nodes:
                indegree[tid] += 1
                dependents[dep].append(tid)

    queue = deque(sorted((tid for tid, d in indegree.items() if d == 0), key=str))
    order: list[uuid.UUID] = []

    while queue:
        current = queue.popleft()
        order.append(current)
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(order) != len(nodes):
        raise DependencyCycle(sorted((tid for tid in nodes if tid not in set(order)), key=str))
    return order


def analyse(nodes: dict[uuid.UUID, TaskNode]) -> GraphAnalysis:
    """Topologically sort, then find the longest weighted path.

    The critical path is the longest chain of *outstanding* work. It is the floor on how
    fast the goal can finish even with unlimited help, because those tasks must happen
    one after another.
    """
    order = topological_order(nodes)

    longest: dict[uuid.UUID, float] = {}
    predecessor: dict[uuid.UUID, uuid.UUID | None] = {}

    for tid in order:
        node = nodes[tid]
        best_prev: uuid.UUID | None = None
        best_len = 0.0
        for dep in node.depends_on:
            if dep in nodes and longest.get(dep, 0.0) > best_len:
                best_len = longest[dep]
                best_prev = dep
        longest[tid] = best_len + node.outstanding_minutes
        predecessor[tid] = best_prev

    critical_path: list[uuid.UUID] = []
    critical_minutes = 0.0
    if longest:
        end = max(longest, key=lambda t: (longest[t], str(t)))
        critical_minutes = longest[end]
        cursor: uuid.UUID | None = end
        while cursor is not None:
            critical_path.append(cursor)
            cursor = predecessor[cursor]
        critical_path.reverse()

    # Drop a trailing all-done chain: a path worth zero minutes is not a critical path.
    if critical_minutes == 0.0:
        critical_path = []

    blocked = {
        tid: [d for d in node.depends_on if d in nodes and not nodes[d].is_done]
        for tid, node in nodes.items()
        if not node.is_done
    }

    return GraphAnalysis(
        order=order,
        critical_path=critical_path,
        critical_path_minutes=critical_minutes,
        total_remaining_minutes=sum(n.outstanding_minutes for n in nodes.values()),
        optional_minutes=sum(
            n.outstanding_minutes for n in nodes.values() if n.is_optional
        ),
        blocked=blocked,
    )


def would_create_cycle(
    nodes: dict[uuid.UUID, TaskNode], task_id: uuid.UUID, depends_on: uuid.UUID
) -> bool:
    """Check an edge *before* writing it.

    Rejecting the edge at the API is far kinder than discovering the cycle later, when
    the prediction engine cannot produce a number and the user cannot see why.
    """
    if task_id == depends_on:
        return True
    # The new edge makes task_id downstream of depends_on. That is a cycle exactly when
    # depends_on is already reachable *from* task_id.
    seen: set[uuid.UUID] = set()
    stack = [task_id]
    while stack:
        current = stack.pop()
        if current == depends_on:
            return True
        if current in seen:
            continue
        seen.add(current)
        for tid, node in nodes.items():
            if current in node.depends_on:
                stack.append(tid)
    return False
