"""GraphRAG: multi-hop answers over the personal knowledge graph (FEATURES-50 #35)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.graph import GraphService

router = APIRouter(prefix="/v1/graph", tags=["graph"])


class AskGraph(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    depth: int = Field(default=2, ge=1, le=3)


@router.post("/answer")
async def answer(body: AskGraph, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    from jarvis.llm.router import LLMRouter

    router_ = LLMRouter(session)
    return await GraphService(session).answer(
        user.id, body.question, depth=body.depth, router=router_
    )
