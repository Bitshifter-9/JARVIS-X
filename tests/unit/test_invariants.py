"""Property-based invariants.

The example-based tests check the cases we thought of. These check properties that must
hold for *every* input, which is where the cases we did not think of live.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from jarvis.db.models.agent import Risk
from jarvis.services.goal.graph import DependencyCycle, TaskNode, analyse, topological_order
from jarvis.services.goal.prediction import (
    calibration_multiplier,
    probability_of_finishing,
    severity_for,
)
from jarvis.services.policy.rules import rule_for
from jarvis.services.tool_gateway.templates import COMMAND_TEMPLATES

# Anything a model could put in an args dict.
json_scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=200),
)
args_dicts = st.dictionaries(st.text(min_size=1, max_size=40), json_scalars, max_size=8)


# ── policy ─────────────────────────────────────────────────────────────
@given(args=args_dicts)
def test_prohibited_tools_stay_prohibited_for_every_argument(args):
    """No combination of arguments can make an R4 tool anything but R4."""
    for tool in ("payment.send", "credentials.export", "audit.disable", "shell.execute"):
        assert rule_for(tool).risk is Risk.R4


@given(tool=st.text(min_size=1, max_size=80))
def test_an_unknown_tool_is_always_prohibited(tool):
    """A tool nobody reviewed must never default to permitted."""
    assume(tool not in COMMAND_TEMPLATES)
    rule = rule_for(tool)
    known = rule.description != "Unregistered tool"
    assert known or rule.risk is Risk.R4


@given(
    risk=st.sampled_from(list(Risk)),
    other=st.sampled_from(list(Risk)),
)
def test_risk_ordering_is_total_and_stable(risk, other):
    order = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
    assert (order[risk.value] < order[other.value]) == (risk.value < other.value)


# ── command templates ──────────────────────────────────────────────────
@given(value=st.text(max_size=300))
def test_template_parameters_never_become_shell_syntax(value):
    """Whatever a model puts in a slot must arrive as exactly one argv entry."""
    argv = COMMAND_TEMPLATES["git.status"].render({"path": value})
    assert argv == ["git", "-C", value, "status", "--short"]
    assert len(argv) == 5


@given(value=st.text(max_size=200))
def test_template_previews_round_trip_through_a_shell_lexer(value):
    """The preview shown for approval must mean the same thing a shell would read."""
    import shlex

    preview = COMMAND_TEMPLATES["git.status"].preview({"path": value})
    assert shlex.split(preview) == ["git", "-C", value, "status", "--short"]


# ── the probability model ──────────────────────────────────────────────
@given(
    available=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    p50=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    spread=st.floats(min_value=1.0, max_value=5.0, allow_nan=False),
)
def test_probability_is_always_a_probability(available, p50, spread):
    result = probability_of_finishing(available, p50, p50 * spread)
    assert 0.0 <= result <= 1.0
    assert not math.isnan(result)


@given(
    p50=st.floats(min_value=1.0, max_value=10_000, allow_nan=False),
    a=st.floats(min_value=0.0, max_value=10_000, allow_nan=False),
    extra=st.floats(min_value=0.0, max_value=10_000, allow_nan=False),
)
def test_more_time_never_lowers_the_probability(p50, a, extra):
    """Monotonicity. A forecast that got worse when you gained an hour is broken."""
    less = probability_of_finishing(a, p50, p50 * 1.4)
    more = probability_of_finishing(a + extra, p50, p50 * 1.4)
    assert more >= less - 1e-9


@given(
    available=st.floats(min_value=1.0, max_value=10_000, allow_nan=False),
    p50=st.floats(min_value=1.0, max_value=10_000, allow_nan=False),
    extra=st.floats(min_value=0.0, max_value=10_000, allow_nan=False),
)
def test_more_work_never_raises_the_probability(available, p50, extra):
    less = probability_of_finishing(available, p50 + extra, (p50 + extra) * 1.4)
    more = probability_of_finishing(available, p50, p50 * 1.4)
    assert more >= less - 1e-9


@given(ratio=st.floats(min_value=0.0, max_value=10.0, allow_nan=False))
def test_severity_is_a_total_function_of_the_ratio(ratio):
    assert severity_for(ratio) in ("critical", "at_risk", "on_track")
    if ratio >= 1.0:
        assert severity_for(ratio) == "on_track"


@given(
    pairs=st.lists(
        st.tuples(
            st.floats(min_value=1.0, max_value=1000, allow_nan=False),
            st.floats(min_value=0.0, max_value=100_000, allow_nan=False),
        ),
        max_size=40,
    )
)
def test_calibration_is_always_within_its_clamp(pairs):
    """A wild ratio is usually a mistyped estimate, and must not distort every forecast."""
    assert 0.5 <= calibration_multiplier(pairs) <= 4.0


# ── the task DAG ───────────────────────────────────────────────────────
@st.composite
def acyclic_graphs(draw):
    """Nodes wired only to earlier nodes, so the graph is acyclic by construction."""
    size = draw(st.integers(min_value=1, max_value=12))
    ids = [uuid.uuid4() for _ in range(size)]
    nodes = {}
    for index, node_id in enumerate(ids):
        deps = draw(
            st.lists(st.sampled_from(ids[:index]), max_size=min(index, 3), unique=True)
        ) if index else []
        nodes[node_id] = TaskNode(
            id=node_id,
            title=f"t{index}",
            remaining_minutes=draw(st.floats(min_value=0, max_value=1000, allow_nan=False)),
            status=draw(st.sampled_from(["open", "in_progress", "done"])),
            is_optional=draw(st.booleans()),
            depends_on=tuple(deps),
        )
    return nodes


@given(nodes=acyclic_graphs())
@settings(max_examples=60)
def test_a_topological_order_always_places_dependencies_first(nodes):
    order = topological_order(nodes)
    position = {node_id: i for i, node_id in enumerate(order)}
    assert len(order) == len(nodes)
    for node_id, node in nodes.items():
        for dep in node.depends_on:
            if dep in nodes:
                assert position[dep] < position[node_id]


@given(nodes=acyclic_graphs())
@settings(max_examples=60)
def test_the_critical_path_never_exceeds_the_total_work(nodes):
    """A chain cannot be longer than everything, and neither can be negative."""
    result = analyse(nodes)
    assert 0 <= result.critical_path_minutes <= result.total_remaining_minutes + 1e-6
    assert result.optional_minutes <= result.total_remaining_minutes + 1e-6


@given(nodes=acyclic_graphs())
@settings(max_examples=40)
def test_completed_work_never_counts_as_remaining(nodes):
    result = analyse(nodes)
    expected = sum(
        n.remaining_minutes for n in nodes.values() if n.status not in ("done", "cancelled")
    )
    assert result.total_remaining_minutes == expected


@given(size=st.integers(min_value=2, max_value=8))
def test_a_cycle_is_always_detected(size):
    ids = [uuid.uuid4() for _ in range(size)]
    nodes = {
        node_id: TaskNode(
            id=node_id, title="t", remaining_minutes=10,
            depends_on=(ids[(index - 1) % size],),
        )
        for index, node_id in enumerate(ids)
    }
    try:
        topological_order(nodes)
    except DependencyCycle as exc:
        assert len(exc.members) == size
    else:
        raise AssertionError("a full cycle went undetected")


# ── approval binding ───────────────────────────────────────────────────
@given(tool=st.text(min_size=1, max_size=60), args=args_dicts)
def test_the_approval_hash_is_stable_for_identical_payloads(tool, args):
    """Two structurally identical payloads must hash identically, or an approval could be
    replayed against a re-serialized copy of itself."""
    from jarvis.core.security import approval_payload_hash

    expiry = datetime.now(UTC) + timedelta(minutes=10)
    kwargs = {
        "tool": tool, "args": args, "user_id": "u1", "device_id": None, "expires_at": expiry
    }
    assert approval_payload_hash(**kwargs) == approval_payload_hash(**kwargs)


@given(
    tool=st.text(min_size=1, max_size=60),
    args=args_dicts,
    other=args_dicts,
)
def test_different_arguments_always_produce_a_different_hash(tool, args, other):
    from jarvis.core.security import approval_payload_hash

    assume(args != other)
    expiry = datetime.now(UTC) + timedelta(minutes=10)
    a = approval_payload_hash(
        tool=tool, args=args, user_id="u1", device_id=None, expires_at=expiry
    )
    b = approval_payload_hash(
        tool=tool, args=other, user_id="u1", device_id=None, expires_at=expiry
    )
    assert a != b
