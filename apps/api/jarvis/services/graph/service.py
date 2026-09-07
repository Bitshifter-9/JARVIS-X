"""The personal knowledge graph.

Plain PostgreSQL tables, per blueprint §8: ``entities``, ``relations``, ``entity_aliases``.
No graph database, because the queries we actually run are two or three hops deep and a
recursive CTE handles those in one round trip against data that already lives here.

The rule the whole thing exists to serve: **every edge carries provenance**, so
"why do you believe this?" always resolves to a source object rather than to a vibe.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.core.errors import Conflict
from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.db.models.ops import Entity, EntityAlias, Relation
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

PREDICATES = frozenset({
    "OWNS", "HAS_GOAL", "BLOCKED_BY", "DEPENDS_ON", "MEMBER_OF",
    "ASSIGNED_TO", "PART_OF", "MENTIONS", "DUE_FOR", "TAUGHT_BY",
    "RELATED_AS",  # a person's relation to the owner: professor, client, family…
})

# A single message is evidence, not proof. Below this an edge is recorded but marked
# unconfirmed, so the UI can show it as "believed" rather than "known".
CONFIRMED_ABOVE = 0.7


@dataclass(frozen=True)
class Belief:
    """An edge plus why we hold it."""

    subject: str
    predicate: str
    object: str
    confidence: float
    provenance: dict[str, Any]
    relation_id: uuid.UUID
    created_at: datetime

    @property
    def confirmed(self) -> bool:
        return self.confidence >= CONFIRMED_ABOVE

    @property
    def source(self) -> str:
        p = self.provenance
        ref = p.get("object_id") or p.get("run_id") or str(self.relation_id)[:8]
        return f"{p.get('source', 'unknown')}:{ref}"

    def explain(self) -> str:
        return (
            f"{self.subject} —{self.predicate}→ {self.object} "
            f"({self.confidence:.0%} confident, from {self.source})"
        )


@dataclass
class Neighbourhood:
    root: str
    nodes: dict[str, str] = field(default_factory=dict)
    edges: list[Belief] = field(default_factory=list)


class GraphService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_entity(
        self,
        user_id: uuid.UUID,
        *,
        kind: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
    ) -> Entity:
        stmt = (
            pg_insert(Entity)
            .values(
                id=uuid7(), user_id=user_id, kind=kind, name=name,
                attributes=attributes or {},
            )
            .on_conflict_do_update(
                index_elements=[Entity.user_id, Entity.kind, Entity.name],
                set_={"attributes": attributes or {}, "updated_at": datetime.now(UTC)},
            )
            .returning(Entity)
        )
        entity = (await self.session.execute(stmt)).scalar_one()

        for alias in aliases or []:
            self.session.add(EntityAlias(entity_id=entity.id, alias=alias))
        await self.session.flush()
        return entity

    async def resolve(self, user_id: uuid.UUID, name: str) -> Entity | None:
        """Find an entity by name or alias.

        Aliases matter because "Dr Sharma", "A. Sharma" and "sharma@uni.edu" are one
        person, and a graph that thinks they are three answers every question wrongly.
        """
        direct = await self.session.scalar(
            select(Entity).where(Entity.user_id == user_id, Entity.name == name)
        )
        if direct is not None:
            return direct

        return await self.session.scalar(
            select(Entity)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id)
            .where(Entity.user_id == user_id, EntityAlias.alias == name)
        )

    async def assert_edge(
        self,
        user_id: uuid.UUID,
        *,
        subject: Entity | str,
        predicate: str,
        obj: Entity | str,
        provenance: dict[str, Any],
        confidence: float = 0.5,
        subject_kind: str = "thing",
        object_kind: str = "thing",
    ) -> Relation:
        """Record a belief. Provenance is required, not optional."""
        if predicate not in PREDICATES:
            raise Conflict(f"Unknown predicate {predicate!r}")
        if not provenance.get("source"):
            raise Conflict("Every edge must record where it came from")

        s = (
            subject
            if isinstance(subject, Entity)
            else await self.upsert_entity(user_id, kind=subject_kind, name=subject)
        )
        o = (
            obj
            if isinstance(obj, Entity)
            else await self.upsert_entity(user_id, kind=object_kind, name=obj)
        )

        existing = await self.session.scalar(
            select(Relation).where(
                Relation.user_id == user_id,
                Relation.subject_id == s.id,
                Relation.predicate == predicate,
                Relation.object_id == o.id,
                Relation.invalidated_at.is_(None),
            )
        )
        if existing is not None:
            # Seeing the same thing again is corroboration, not a duplicate. Confidence
            # rises toward certainty without ever reaching it from repetition alone.
            existing.confidence = min(0.99, existing.confidence + (1 - existing.confidence) * 0.4)
            existing.provenance = {
                **existing.provenance,
                "corroborations": existing.provenance.get("corroborations", 1) + 1,
                "latest": provenance,
            }
            await self.session.flush()
            return existing

        relation = Relation(
            user_id=user_id,
            subject_id=s.id,
            predicate=predicate,
            object_id=o.id,
            provenance=provenance,
            confidence=confidence,
        )
        self.session.add(relation)
        await self.session.flush()
        log.info("graph_edge_asserted", predicate=predicate, confidence=confidence)
        return relation

    async def retract(self, user_id: uuid.UUID, relation_id: uuid.UUID, *, reason: str) -> bool:
        """Invalidate an edge rather than deleting it.

        A correction should leave a trace: "we used to think X, and here is why we
        stopped" is more useful than a graph that silently rearranges itself.
        """
        relation = await self.session.scalar(
            select(Relation).where(Relation.id == relation_id, Relation.user_id == user_id)
        )
        if relation is None or relation.invalidated_at is not None:
            return False

        relation.invalidated_at = datetime.now(UTC)
        relation.provenance = {**relation.provenance, "retracted_because": reason}
        await self.session.flush()
        return True

    async def beliefs_about(
        self, user_id: uuid.UUID, name: str, *, include_incoming: bool = True
    ) -> list[Belief]:
        """Everything currently believed about one entity, with its evidence."""
        entity = await self.resolve(user_id, name)
        if entity is None:
            return []

        rows = (
            await self.session.execute(
                text("""
                    SELECT r.id, r.predicate, r.confidence, r.provenance, r.created_at,
                           s.name AS subject, o.name AS object
                    FROM relations r
                    JOIN entities s ON s.id = r.subject_id
                    JOIN entities o ON o.id = r.object_id
                    WHERE r.user_id = CAST(:uid AS uuid)
                      AND r.invalidated_at IS NULL
                      AND (
                            r.subject_id = :eid
                            OR (CAST(:incoming AS boolean) AND r.object_id = :eid)
                          )
                    ORDER BY r.confidence DESC, r.created_at DESC
                """),
                {"uid": str(user_id), "eid": entity.id, "incoming": include_incoming},
            )
        ).mappings().all()

        return [_belief(r) for r in rows]

    async def neighbourhood(
        self, user_id: uuid.UUID, name: str, *, depth: int = 2
    ) -> Neighbourhood:
        """Walk outward from an entity.

        One recursive CTE rather than N round trips — the reason a relational graph is
        adequate here at all.
        """
        entity = await self.resolve(user_id, name)
        if entity is None:
            return Neighbourhood(root=name)

        rows = (
            await self.session.execute(
                text("""
                    WITH RECURSIVE walk(relation_id, subject_id, object_id, depth) AS (
                        SELECT r.id, r.subject_id, r.object_id, 1
                        FROM relations r
                        WHERE r.user_id = CAST(:uid AS uuid)
                          AND r.invalidated_at IS NULL
                          AND (r.subject_id = :eid OR r.object_id = :eid)
                        UNION
                        SELECT r.id, r.subject_id, r.object_id, w.depth + 1
                        FROM relations r
                        JOIN walk w
                          ON r.subject_id IN (w.subject_id, w.object_id)
                          OR r.object_id IN (w.subject_id, w.object_id)
                        WHERE r.user_id = CAST(:uid AS uuid)
                          AND r.invalidated_at IS NULL
                          AND w.depth < :depth
                    )
                    SELECT DISTINCT r.id, r.predicate, r.confidence, r.provenance,
                           r.created_at, s.name AS subject, s.kind AS subject_kind,
                           o.name AS object, o.kind AS object_kind
                    FROM walk w
                    JOIN relations r ON r.id = w.relation_id
                    JOIN entities s ON s.id = r.subject_id
                    JOIN entities o ON o.id = r.object_id
                    ORDER BY r.confidence DESC
                """),
                {"uid": str(user_id), "eid": entity.id, "depth": depth},
            )
        ).mappings().all()

        result = Neighbourhood(root=entity.name)
        for row in rows:
            result.nodes[row["subject"]] = row["subject_kind"]
            result.nodes[row["object"]] = row["object_kind"]
            result.edges.append(_belief(row))
        return result

    async def _entities_in(self, user_id: uuid.UUID, question: str) -> list[str]:
        """Entity names mentioned in a question, resolved through the graph's aliases.
        Tries 1–3 word windows so "Dr Sharma" resolves as one name."""
        words = re.findall(r"[A-Za-z0-9@.'-]+", question)
        seen_ids: set[uuid.UUID] = set()
        names: list[str] = []
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                if len(phrase) < 3:
                    continue
                entity = await self.resolve(user_id, phrase)
                if entity is not None and entity.id not in seen_ids:
                    seen_ids.add(entity.id)
                    names.append(entity.name)
        return names

    async def answer(
        self, user_id: uuid.UUID, question: str, *, depth: int = 2, router=None  # noqa: ANN001
    ) -> dict[str, Any]:
        """GraphRAG (FEATURES-50 #35): find the entities a question is about, walk the
        graph up to ``depth`` hops, and answer from the beliefs found — each with its
        provenance, so the answer is auditable. No model → the facts are returned as-is."""
        names = await self._entities_in(user_id, question)
        facts: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for name in names:
            nb = await self.neighbourhood(user_id, name, depth=depth)
            for b in nb.edges:
                key = (b.subject, b.predicate, b.object)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    {
                        "subject": b.subject,
                        "predicate": b.predicate,
                        "object": b.object,
                        "confidence": round(b.confidence, 2),
                        "source": b.source,
                        "confirmed": b.confirmed,
                    }
                )
        answer = await self._synthesise(question, facts, router) if facts else None
        return {"answer": answer, "facts": facts, "entities": names}

    async def _synthesise(
        self, question: str, facts: list[dict[str, Any]], router  # noqa: ANN001
    ) -> str:
        lines = [f"- {f['subject']} {f['predicate']} {f['object']}" for f in facts[:20]]
        if router is None:
            return "From what I know:\n" + "\n".join(lines)
        from jarvis.llm.types import CallClass, LLMRequest, Message

        prompt = (
            "Answer the question using ONLY these facts from the user's knowledge graph. "
            "If they do not answer it, say so. Be concise.\n\n"
            f"Facts:\n{chr(10).join(lines)}\n\nQuestion: {question}"
        )
        try:
            response = await router.generate(
                LLMRequest(
                    call_class=CallClass.CHAT,
                    messages=[Message("user", prompt)],
                    max_tokens=300,
                    temperature=0.2,
                )
            )
            return response.text.strip()
        except Exception:  # noqa: BLE001 — fall back to the raw facts
            return "From what I know:\n" + "\n".join(lines)

    async def why(self, user_id: uuid.UUID, subject: str, predicate: str, obj: str) -> str:
        """Answer "why do you believe this?" in one sentence, or admit we do not."""
        beliefs = await self.beliefs_about(user_id, subject)
        match = next(
            (b for b in beliefs if b.predicate == predicate and b.object == obj), None
        )
        if match is None:
            return f"No current belief that {subject} —{predicate}→ {obj}."

        corroborations = match.provenance.get("corroborations", 1)
        seen = "once" if corroborations == 1 else f"{corroborations} times"
        confidence = "believed" if not match.confirmed else "confirmed"
        return (
            f"{confidence.capitalize()}: {match.explain()}. Seen {seen}."
        )


def _belief(row: Any) -> Belief:
    return Belief(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        confidence=float(row["confidence"]),
        provenance=row["provenance"] or {},
        relation_id=row["id"],
        created_at=row["created_at"],
    )
