"""Memory: four tiers, one database, retrieval that filters before it searches.

The ordering in ``retrieve`` is the whole design (blueprint §8). Tenant, scope and
retention are filtered **in SQL first**, and pgvector similarity runs only over what
survived. Searching the whole corpus and filtering afterwards would be both slower and a
tenant-isolation bug waiting to happen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.db.models.ops import Memory
from jarvis.services.memory.embeddings import Embedder, get_embedder
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

# Never surfaced to a model, whatever tier they were written into.
_SECRET_MARKERS = ("password", "api_key", "secret", "token", "authorization")


@dataclass(frozen=True)
class Recollection:
    id: uuid.UUID
    content: str
    kind: str
    score: float
    provenance: dict[str, Any]
    created_at: datetime

    @property
    def citation(self) -> str:
        source = self.provenance.get("source") or self.kind
        ref = self.provenance.get("object_id") or self.provenance.get("task_id") or str(self.id)[:8]
        return f"{source}:{ref}"


class MemoryService:
    def __init__(self, session: AsyncSession, embedder: Embedder | None = None) -> None:
        self.session = session
        self.embedder = embedder or get_embedder()

    async def remember(
        self,
        user_id: uuid.UUID,
        *,
        content: str,
        kind: str = "semantic",
        provenance: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> Memory | None:
        """Write a memory, refusing anything that looks like a credential.

        The agent may *propose* a memory; this is the reducer that decides. Refusing here
        is cheaper than discovering a token in a prompt later.
        """
        if _looks_like_a_secret(content):
            log.warning("memory_refused_secret_like", kind=kind)
            return None

        vector = self.embedder.embed([content])[0]
        memory = Memory(
            user_id=user_id,
            kind=kind,
            content=content,
            embedding=vector,
            provenance=provenance or {},
            importance=importance,
            valid_from=datetime.now(UTC),
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def correct(
        self, user_id: uuid.UUID, memory_id: uuid.UUID, *, content: str
    ) -> Memory | None:
        """Supersede rather than overwrite, so the old belief stays auditable."""
        previous = await self.session.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )
        if previous is None:
            return None

        replacement = await self.remember(
            user_id,
            content=content,
            kind=previous.kind,
            provenance={**previous.provenance, "corrects": str(previous.id)},
            importance=previous.importance,
        )
        if replacement is not None:
            previous.invalidated_at = datetime.now(UTC)
            previous.superseded_by = replacement.id
            await self.session.flush()
        return replacement

    async def retrieve(
        self,
        user_id: uuid.UUID,
        query: str,
        *,
        limit: int = 5,
        kinds: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[Recollection]:
        vector = self.embedder.embed([query])[0]

        rows = (
            await self.session.execute(
                text("""
                    SELECT id, content, kind, provenance, created_at, importance,
                           1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM memories
                    WHERE user_id = CAST(:user_id AS uuid)
                      AND invalidated_at IS NULL
                      AND embedding IS NOT NULL
                      AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
                    ORDER BY embedding <=> CAST(:vec AS vector)
                    LIMIT :limit
                """),
                {
                    "vec": str(vector),
                    "user_id": str(user_id),
                    "kinds": kinds,
                    "limit": limit * 3,
                },
            )
        ).mappings().all()

        scored = [
            Recollection(
                id=r["id"],
                content=r["content"],
                kind=r["kind"],
                score=_rerank(r["similarity"], r["importance"], r["created_at"]),
                provenance=r["provenance"] or {},
                created_at=r["created_at"],
            )
            for r in rows
        ]
        scored.sort(key=lambda r: -r.score)
        return [r for r in scored if r.score >= min_score][:limit]

    async def context_for(self, user_id: uuid.UUID, query: str, *, limit: int = 5) -> str:
        """Retrieved memory as prompt text, with citations and no credentials."""
        found = await self.retrieve(user_id, query, limit=limit)
        if not found:
            return ""
        lines = [f"- [{r.citation}] {r.content}" for r in found]
        return "Relevant memory:\n" + "\n".join(lines)


def _rerank(similarity: float, importance: float, created_at: datetime) -> float:
    """Relevance, recency and importance — blueprint §8's rerank, made explicit.

    Similarity dominates; the others break ties. A three-month-old fact that is a perfect
    match should still beat a vaguely-related one from this morning.
    """
    age_days = max(0.0, (datetime.now(UTC) - created_at).total_seconds() / 86400)
    recency = 1.0 / (1.0 + age_days / 30.0)
    return 0.7 * similarity + 0.2 * importance + 0.1 * recency


def _looks_like_a_secret(content: str) -> bool:
    lowered = content.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return True
    # Long unbroken high-entropy runs are how keys look.
    return any(len(token) > 40 and not token.isalpha() for token in content.split())
