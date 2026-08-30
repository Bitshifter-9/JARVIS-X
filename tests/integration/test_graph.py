"""Phase 3.4 gate: the knowledge graph.

Exit test from PLAN.md §12: "why do you believe this?" resolves to a source.
"""

from __future__ import annotations

import pytest
from jarvis.core.errors import Conflict
from jarvis.services.graph import CONFIRMED_ABOVE, GraphService
from jarvis.services.identity import IdentityService

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
GMAIL = {"source": "gmail", "object_id": "msg-18c9f"}


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("graph@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
def graph(session):
    return GraphService(session)


# ── provenance is the point ────────────────────────────────────────────
async def test_an_edge_without_a_source_is_refused(session, user, graph):
    """An unsourced edge cannot answer "why", so it is not allowed to exist."""
    with pytest.raises(Conflict, match="where it came from"):
        await graph.assert_edge(
            user.id, subject="Pranav", predicate="OWNS", obj="JARVIS X", provenance={}
        )


async def test_why_resolves_to_a_source(session, user, graph):
    await graph.assert_edge(
        user.id,
        subject="Hackathon Submission",
        predicate="BLOCKED_BY",
        obj="Alexa Certification",
        provenance=GMAIL,
        confidence=0.8,
    )
    await session.commit()

    answer = await graph.why(
        user.id, "Hackathon Submission", "BLOCKED_BY", "Alexa Certification"
    )
    assert "gmail:msg-18c9f" in answer
    assert "Confirmed" in answer
    assert "80%" in answer


async def test_why_admits_when_there_is_no_belief(session, user, graph):
    answer = await graph.why(user.id, "Pranav", "OWNS", "A Yacht")
    assert "No current belief" in answer


async def test_an_unknown_predicate_is_refused(session, user, graph):
    with pytest.raises(Conflict, match="Unknown predicate"):
        await graph.assert_edge(
            user.id, subject="a", predicate="VIBES_WITH", obj="b", provenance=GMAIL
        )


# ── confidence ─────────────────────────────────────────────────────────
async def test_one_message_is_evidence_not_proof(session, user, graph):
    """Blueprint §8: do not silently infer a permanent fact from a single message."""
    relation = await graph.assert_edge(
        user.id, subject="Pranav", predicate="MEMBER_OF", obj="Team Alpha",
        provenance=GMAIL, confidence=0.4,
    )
    await session.commit()
    assert relation.confidence < CONFIRMED_ABOVE

    beliefs = await graph.beliefs_about(user.id, "Pranav")
    assert beliefs[0].confirmed is False


async def test_corroboration_raises_confidence_without_reaching_certainty(session, user, graph):
    for i in range(8):
        await graph.assert_edge(
            user.id, subject="Pranav", predicate="MEMBER_OF", obj="Team Alpha",
            provenance={"source": "gmail", "object_id": f"msg-{i}"}, confidence=0.4,
        )
    await session.commit()

    belief = (await graph.beliefs_about(user.id, "Pranav"))[0]
    assert belief.confirmed is True
    assert belief.confidence < 1.0, "repetition alone never reaches certainty"
    assert belief.provenance["corroborations"] == 8


async def test_corroboration_keeps_the_latest_source(session, user, graph):
    await graph.assert_edge(
        user.id, subject="a", predicate="OWNS", obj="b",
        provenance={"source": "gmail", "object_id": "first"},
    )
    await graph.assert_edge(
        user.id, subject="a", predicate="OWNS", obj="b",
        provenance={"source": "slack", "object_id": "second"},
    )
    await session.commit()

    belief = (await graph.beliefs_about(user.id, "a"))[0]
    assert belief.provenance["latest"]["object_id"] == "second"


# ── aliases ────────────────────────────────────────────────────────────
async def test_aliases_resolve_to_one_entity(session, user, graph):
    """Three names for one person is three wrong answers to every question."""
    await graph.upsert_entity(
        user.id, kind="person", name="Dr A. Sharma",
        aliases=["Dr Sharma", "sharma@uni.edu", "A. Sharma"],
    )
    await session.commit()

    for name in ["Dr A. Sharma", "Dr Sharma", "sharma@uni.edu", "A. Sharma"]:
        entity = await graph.resolve(user.id, name)
        assert entity is not None and entity.name == "Dr A. Sharma", name


async def test_an_unknown_name_resolves_to_nothing(session, user, graph):
    assert await graph.resolve(user.id, "Nobody At All") is None


# ── retraction ─────────────────────────────────────────────────────────
async def test_retracting_leaves_a_trace(session, user, graph):
    """"We used to think X, and here is why we stopped" beats silent rearrangement."""
    relation = await graph.assert_edge(
        user.id, subject="Hackathon", predicate="BLOCKED_BY", obj="Alexa Certification",
        provenance=GMAIL, confidence=0.9,
    )
    await session.commit()

    assert await graph.retract(user.id, relation.id, reason="certification granted") is True
    await session.commit()

    assert await graph.beliefs_about(user.id, "Hackathon") == []
    await session.refresh(relation)
    assert relation.invalidated_at is not None
    assert relation.provenance["retracted_because"] == "certification granted"


async def test_retracting_twice_is_not_an_error(session, user, graph):
    relation = await graph.assert_edge(
        user.id, subject="a", predicate="OWNS", obj="b", provenance=GMAIL
    )
    await session.commit()
    assert await graph.retract(user.id, relation.id, reason="x") is True
    assert await graph.retract(user.id, relation.id, reason="x") is False


# ── traversal ──────────────────────────────────────────────────────────
async def test_the_blueprint_example_walks_end_to_end(session, user, graph):
    """Pranav —OWNS→ JARVIS X —HAS_GOAL→ Submission —BLOCKED_BY→ Certification."""
    chain = [
        ("Pranav", "OWNS", "JARVIS X"),
        ("JARVIS X", "HAS_GOAL", "Hackathon Submission"),
        ("Hackathon Submission", "BLOCKED_BY", "Alexa Certification"),
    ]
    for subject, predicate, obj in chain:
        await graph.assert_edge(
            user.id, subject=subject, predicate=predicate, obj=obj,
            provenance=GMAIL, confidence=0.9,
        )
    await session.commit()

    hood = await graph.neighbourhood(user.id, "Pranav", depth=3)
    assert "Alexa Certification" in hood.nodes, "three hops out from the root"
    assert {(e.subject, e.predicate, e.object) for e in hood.edges} == set(chain)


async def test_depth_bounds_the_walk(session, user, graph):
    for subject, obj in [("a", "b"), ("b", "c"), ("c", "d")]:
        await graph.assert_edge(
            user.id, subject=subject, predicate="DEPENDS_ON", obj=obj, provenance=GMAIL
        )
    await session.commit()

    shallow = await graph.neighbourhood(user.id, "a", depth=1)
    assert "d" not in shallow.nodes

    deep = await graph.neighbourhood(user.id, "a", depth=3)
    assert "d" in deep.nodes


async def test_a_retracted_edge_is_not_walked(session, user, graph):
    first = await graph.assert_edge(
        user.id, subject="a", predicate="DEPENDS_ON", obj="b", provenance=GMAIL
    )
    await graph.assert_edge(
        user.id, subject="b", predicate="DEPENDS_ON", obj="c", provenance=GMAIL
    )
    await session.commit()

    await graph.retract(user.id, first.id, reason="wrong")
    await session.commit()

    hood = await graph.neighbourhood(user.id, "a", depth=3)
    assert hood.edges == []


async def test_a_cycle_does_not_hang_the_walk(session, user, graph):
    """A recursive CTE over a cyclic graph must terminate."""
    for subject, obj in [("a", "b"), ("b", "c"), ("c", "a")]:
        await graph.assert_edge(
            user.id, subject=subject, predicate="DEPENDS_ON", obj=obj, provenance=GMAIL
        )
    await session.commit()

    hood = await graph.neighbourhood(user.id, "a", depth=4)
    assert set(hood.nodes) == {"a", "b", "c"}


async def test_the_graph_is_scoped_to_its_owner(session, user, graph):
    other = await IdentityService(session).register("intruder@example.com", PASSWORD)
    await graph.assert_edge(
        user.id, subject="Pranav", predicate="OWNS", obj="Private Thing", provenance=GMAIL
    )
    await session.commit()

    assert await graph.beliefs_about(other.id, "Pranav") == []
    assert (await graph.neighbourhood(other.id, "Pranav")).edges == []


async def test_walking_an_unknown_entity_is_empty_not_an_error(session, user, graph):
    hood = await graph.neighbourhood(user.id, "Nobody")
    assert hood.nodes == {} and hood.edges == []
